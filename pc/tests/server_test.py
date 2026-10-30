"""Phone-flow test against a running ScanStation (with the fake COLMAP).

    python tests/server_test.py SYNTH_OUT_DIR WORK_DIR
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
synth, work = Path(sys.argv[1]), Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
cfg_path = work / "config.json"
cfg_path.write_text(
    json.dumps(
        dict(
            port=18765,
            discovery_port=18766,
            pairing_code="424242",
            scans_dir=str(work / "scans"),
            colmap_path=str(HERE / "fake_colmap.py"),
        )
    )
)
env = dict(os.environ, SCANSTATION_CONFIG=str(cfg_path), PYTHONUNBUFFERED="1")
srv = subprocess.Popen(
    [sys.executable, str(HERE.parent / "scanstation.py")], env=env, stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT
)
BASE = "http://127.0.0.1:18765"
KEY = {"X-Scan-Key": "424242"}


def req(method, path, data=None, headers=None, raw=False):
    h = dict(KEY, **(headers or {}))
    body = data
    if isinstance(data, dict):
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=60) as resp:
        out = resp.read()
    return out if raw else json.loads(out)


try:
    for _ in range(50):
        try:
            info = req("GET", "/api/info")
            break
        except Exception:
            time.sleep(0.3)
    print("info:", info)
    assert info["key_ok"]

    # wrong key -> 401
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/api/scans", headers={"X-Scan-Key": "1"}))
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401, e.code

    # discovery
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(b"SCAN3D_DISCOVER", ("127.0.0.1", 18766))
    print("discovery:", s.recvfrom(256)[0])

    st = req("POST", "/api/scans", {"name": "box test", "client_id": "abc-123", "meta": {"device": "test"}})
    sid = st["id"]
    again = req("POST", "/api/scans", {"name": "box test", "client_id": "abc-123"})
    assert again["id"] == sid, "same client_id must reuse the scan"
    imgs = sorted((synth / "images").glob("*.jpg"))
    for p in imgs:
        r = req("PUT", f"/api/scans/{sid}/images/{p.name}", p.read_bytes(), {"Content-Type": "image/jpeg"})
    print("uploaded", r)
    req("POST", f"/api/scans/{sid}/process", {})
    t0 = time.time()
    last = None
    while True:
        st = req("GET", f"/api/scans/{sid}")
        msg = (st["state"], st.get("stage_label"), round(st.get("progress", 0), 2))
        if msg != last:
            print("  ", msg)
            last = msg
        if st["state"] in ("done", "error"):
            break
        if time.time() - t0 > 1200:
            raise TimeoutError
        time.sleep(1)
    print("final:", json.dumps(st, indent=1)[:1500])
    assert st["state"] == "done", st.get("message")
    stl = req("GET", f"/api/scans/{sid}/file/model.stl", raw=True)
    png = req("GET", f"/api/scans/{sid}/file/preview.png", raw=True)
    (work / "dl.stl").write_bytes(stl)
    print(f"STL {len(stl):,} bytes, preview {len(png):,} bytes")
finally:
    srv.terminate()
