"""LAN server the phone app talks to: receives photos, queues processing,
reports progress and serves the finished STL. Also answers UDP discovery."""
from __future__ import annotations

import html
import json
import os
import queue
import re
import socket
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import VERSION, config, mat
from .pipeline import IMAGE_EXT, Pipeline

DISCOVER_MSG = b"SCAN3D_DISCOVER"
MAX_IMAGE_BYTES = 60 * 1024 * 1024
SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(s: str, fallback: str = "scan") -> str:
    s = SAFE.sub("_", s.strip())[:60].strip("._")
    return s or fallback


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class Station:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dir = config.scans_dir(cfg)
        self.lock = threading.Lock()
        self.q: queue.Queue[str] = queue.Queue()
        self.waiting: list[str] = []
        # anything interrupted by a restart goes back to "error" so it can be re-sent
        for d in self.dir.iterdir():
            st = read_json(d / "status.json")
            if st and st.get("state") in ("queued", "running"):
                st.update(state="error", message="ScanStation was restarted during processing - tap Process again.")
                write_json(d / "status.json", st)
        threading.Thread(target=self._worker, daemon=True).start()

    # ------------------------------------------------------------ scans
    def scan_dir(self, scan_id: str) -> Path | None:
        d = self.dir / safe_name(scan_id)
        return d if d.is_dir() else None

    def create(self, name: str, client_id: str, meta: dict) -> str:
        with self.lock:
            if client_id:
                for d in self.dir.iterdir():
                    m = read_json(d / "meta.json", {})
                    if m.get("client_id") == client_id:
                        return d.name
            base = safe_name(name)
            sid, n = base, 2
            while (self.dir / sid).exists():
                sid, n = f"{base}-{n}", n + 1
            d = self.dir / sid
            (d / "images").mkdir(parents=True)
            write_json(d / "meta.json", dict(meta, name=name, client_id=client_id, created=time.time()))
            write_json(d / "status.json", dict(state="receiving", progress=0.0, message="Receiving photos"))
            return sid

    def status(self, sid: str) -> dict:
        d = self.dir / sid
        st = read_json(d / "status.json", {}) or {}
        st["id"] = sid
        st["photos"] = sum(1 for p in (d / "images").glob("*") if p.suffix.lower() in IMAGE_EXT)
        if st.get("state") == "queued":
            with self.lock:
                st["queue_position"] = self.waiting.index(sid) + 1 if sid in self.waiting else 1
        if st.get("state") == "done":
            st["report"] = read_json(d / "report.json", {})
        return st

    def set_status(self, sid: str, st: dict) -> None:
        write_json(self.dir / sid / "status.json", st)

    def enqueue(self, sid: str) -> None:
        with self.lock:
            if sid in self.waiting:
                return
            st = read_json(self.dir / sid / "status.json", {}) or {}
            if st.get("state") == "running":
                return
            self.waiting.append(sid)
        self.set_status(sid, dict(state="queued", progress=0.0, message="Waiting for the GPU"))
        self.q.put(sid)

    def _worker(self) -> None:
        while True:
            sid = self.q.get()
            try:
                self.set_status(sid, dict(state="running", progress=0.0, message="Starting"))
                Pipeline(self.dir / sid, self.cfg, on_status=lambda s, sid=sid: self.set_status(sid, s)).run()
            except Exception:
                traceback.print_exc()
            finally:
                with self.lock:
                    if sid in self.waiting:
                        self.waiting.remove(sid)


def make_handler(station: Station, cfg: dict):
    key = str(cfg["pairing_code"])

    class Handler(BaseHTTPRequestHandler):
        server_version = f"ScanStation/{VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter console
            if not self.path.startswith("/api/scans/") or self.command != "GET":
                print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}")

        # ---------------------------------------------------- helpers
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _err(self, code: int, msg: str) -> None:
            # the request body may be unread: close so it can't corrupt the next request
            self.close_connection = True
            self._send(code, json.dumps({"error": msg}).encode(), "application/json", {"Connection": "close"})

        def _authed(self) -> bool:
            if self.headers.get("X-Scan-Key", "") == key:
                return True
            self._err(HTTPStatus.UNAUTHORIZED, "Wrong pairing code - check the code shown in the ScanStation window.")
            return False

        def _body(self, limit: int = 1 << 20) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        def _send_file(self, path: Path, ctype: str, download_name: str | None = None) -> None:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            if download_name:
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
            self.end_headers()
            with open(path, "rb") as f:
                while chunk := f.read(1 << 20):
                    self.wfile.write(chunk)

        def _parts(self) -> list[str]:
            return [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]

        # ---------------------------------------------------- GET
        def do_GET(self):
            parts = self._parts()
            try:
                if not parts:
                    return self._index()
                if parts == ["api", "info"]:
                    return self._json(
                        dict(
                            app="ScanStation",
                            version=VERSION,
                            name=socket.gethostname(),
                            key_ok=self.headers.get("X-Scan-Key", "") == key,
                        )
                    )
                if parts[:1] == ["mat"] and len(parts) == 2:
                    size = parts[1].replace(".pdf", "").upper()
                    if size not in mat.MATS:
                        return self._err(404, "unknown mat")
                    p = config.ROOT / f"Scan3D-mat-{size}.pdf"
                    if not p.exists():
                        mat.make_pdf(str(p), size)
                    return self._send_file(p, "application/pdf")
                if parts[:2] == ["api", "scans"]:
                    if not self._authed():
                        return
                    if len(parts) == 2:
                        items = sorted(
                            (d for d in station.dir.iterdir() if d.is_dir()),
                            key=lambda d: d.stat().st_mtime,
                            reverse=True,
                        )
                        return self._json([station.status(d.name) for d in items[:50]])
                    sid = safe_name(parts[2])
                    d = station.scan_dir(sid)
                    if not d:
                        return self._err(404, "No such scan on this PC")
                    if len(parts) == 3:
                        return self._json(station.status(sid))
                    if len(parts) == 5 and parts[3] == "file":
                        which = parts[4]
                        files = {
                            "model.stl": (d / f"{sid}.stl", "model/stl", f"{sid}.stl"),
                            "preview.png": (d / "preview.png", "image/png", None),
                            "report.json": (d / "report.json", "application/json", None),
                            "log.txt": (d / "pipeline.log", "text/plain; charset=utf-8", None),
                        }
                        if which not in files or not files[which][0].exists():
                            return self._err(404, "File not ready")
                        p, ctype, dl = files[which]
                        return self._send_file(p, ctype, dl)
                return self._err(404, "Not found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        # ---------------------------------------------------- POST / PUT
        def do_POST(self):
            parts = self._parts()
            if parts[:2] != ["api", "scans"] or not self._authed():
                if parts[:2] != ["api", "scans"]:
                    self._err(404, "Not found")
                return
            try:
                body = json.loads(self._body() or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._err(400, "Bad JSON")
            if len(parts) == 2:
                sid = station.create(
                    str(body.get("name") or "scan"), str(body.get("client_id") or ""), body.get("meta") or {}
                )
                return self._json(station.status(sid), 201)
            sid = safe_name(parts[2])
            if not station.scan_dir(sid):
                return self._err(404, "No such scan on this PC")
            if len(parts) == 4 and parts[3] == "process":
                st = station.status(sid)
                if st["photos"] < 15:
                    return self._err(400, f"Only {st['photos']} photos received.")
                station.enqueue(sid)
                return self._json(station.status(sid))
            return self._err(404, "Not found")

        def do_PUT(self):
            parts = self._parts()
            if len(parts) != 5 or parts[:2] != ["api", "scans"] or parts[3] != "images":
                return self._err(404, "Not found")
            if not self._authed():
                return
            sid = safe_name(parts[2])
            d = station.scan_dir(sid)
            if not d:
                return self._err(404, "No such scan on this PC")
            fname = safe_name(parts[4], "photo.jpg")
            if Path(fname).suffix.lower() not in IMAGE_EXT:
                return self._err(400, "Only .jpg/.png photos")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_IMAGE_BYTES:
                return self._err(400, "Bad photo size")
            st = station.status(sid)
            if st.get("state") in ("queued", "running"):
                return self._err(409, "This scan is being processed")
            tmp = d / "images" / (fname + ".part")
            got = 0
            with open(tmp, "wb") as f:
                while got < n:
                    chunk = self.rfile.read(min(1 << 20, n - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got != n:
                tmp.unlink(missing_ok=True)
                return self._err(400, "Upload interrupted")
            os.replace(tmp, d / "images" / fname)
            if st.get("state") in ("done", "error"):
                station.set_status(sid, dict(state="receiving", progress=0.0, message="Receiving photos"))
            return self._json({"ok": True, "photos": station.status(sid)["photos"]})

        # ---------------------------------------------------- PC browser page
        def _index(self):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._send(200, b"ScanStation is running.", "text/plain")
            rows = []
            for d in sorted((d for d in station.dir.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True):
                st = station.status(d.name)
                size = st.get("report", {}).get("size_mm")
                rows.append(
                    f"<tr><td>{html.escape(d.name)}</td><td>{st.get('photos')}</td>"
                    f"<td>{html.escape(str(st.get('state')))}</td><td>{html.escape(st.get('message', ''))}</td>"
                    f"<td>{' x '.join(f'{v:.2f}' for v in size) + ' mm' if size else ''}</td></tr>"
                )
            page = f"""<!doctype html><meta charset=utf-8><title>ScanStation</title>
<meta http-equiv=refresh content=5>
<style>body{{font-family:system-ui;margin:2em}}td,th{{padding:.3em .8em;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>ScanStation {VERSION}</h1><p>Pairing code: <b>{key}</b> &nbsp; Scans folder: <code>{html.escape(str(station.dir))}</code></p>
<p>Printable mats: <a href=/mat/A4.pdf>A4</a> &middot; <a href=/mat/A3.pdf>A3</a></p>
<table><tr><th>Scan</th><th>Photos</th><th>State</th><th>Message</th><th>Size</th></tr>{''.join(rows)}</table>"""
            self._send(200, page.encode(), "text/html; charset=utf-8")

    return Handler


def _discovery(cfg: dict) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", int(cfg["discovery_port"])))
    except OSError as e:
        print(f"(Auto-discovery unavailable: {e}. Enter the PC address on the phone instead.)")
        return
    reply = json.dumps(dict(app="ScanStation", name=socket.gethostname(), port=int(cfg["port"]))).encode()
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.strip() == DISCOVER_MSG:
                sock.sendto(reply, addr)
        except OSError:
            time.sleep(0.5)


def local_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def serve(cfg: dict) -> None:
    station = Station(cfg)
    threading.Thread(target=_discovery, args=(cfg,), daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", int(cfg["port"])), make_handler(station, cfg))
    httpd.daemon_threads = True
    print("=" * 64)
    print(f" ScanStation {VERSION} is running - leave this window open.")
    print(f" Pairing code for the phone app:  {cfg['pairing_code']}")
    for ip in local_ips():
        print(f" PC address:  {ip}:{cfg['port']}")
    print(f" Finished STL files go to: {station.dir}")
    print(" If Windows asks about network access, allow it on Private networks.")
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
