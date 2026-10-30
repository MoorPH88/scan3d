"""ScanStation - turns Scan3D phone photos into true-scale STL files.

    python scanstation.py                 start the server for the phone app
    python scanstation.py process FOLDER  process a folder of photos directly
    python scanstation.py mat             write the printable mats (A4 + A3)
    python scanstation.py check           check that everything is installed
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scanlib import VERSION, config, mat


def cmd_mat(_args) -> None:
    for size in mat.MATS:
        p = config.ROOT / f"Scan3D-mat-{size}.pdf"
        mat.make_pdf(str(p), size)
        print(f"Wrote {p}")


def cmd_check(_args) -> int:
    from scanlib.pipeline import find_colmap

    cfg = config.load()
    ok = True
    print(f"ScanStation {VERSION}, Python {sys.version.split()[0]}")
    for mod in ("numpy", "cv2", "open3d", "trimesh", "scipy", "reportlab", "matplotlib", "mapbox_earcut", "shapely", "rtree"):
        try:
            __import__(mod)
            print(f"  [ok] {mod}")
        except Exception as e:  # noqa: BLE001
            print(f"  [!!] {mod}: {e}")
            ok = False
    exe = find_colmap(cfg)
    if exe:
        r = subprocess.run([exe, "help"], capture_output=True, text=True, errors="replace")
        head = (r.stdout + r.stderr).strip().splitlines()[:2]
        print(f"  [ok] COLMAP: {exe}")
        for line in head:
            print(f"       {line}")
        if "without CUDA" in (r.stdout + r.stderr):
            print("  [!!] This COLMAP build has no CUDA - dense reconstruction will fail.")
            ok = False
    else:
        print("  [!!] COLMAP not found (run setup.bat)")
        ok = False
    smi = shutil.which("nvidia-smi")
    if smi:
        r = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], capture_output=True, text=True)
        print(f"  [ok] GPU: {r.stdout.strip()}")
    else:
        print("  [!!] nvidia-smi not found - is the NVIDIA driver installed?")
        ok = False
    print("All good." if ok else "Some checks failed (see above).")
    return 0 if ok else 1


def cmd_process(args) -> int:
    from scanlib.pipeline import IMAGE_EXT, Pipeline
    from scanlib.server import safe_name

    cfg = config.load()
    src = Path(args.folder)
    photos = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXT)
    if not photos:
        print(f"No photos in {src}")
        return 1
    name = safe_name(args.name or src.name)
    scan = config.scans_dir(cfg) / name
    if scan.exists() and not args.overwrite:
        name = f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        scan = config.scans_dir(cfg) / name
    (scan / "images").mkdir(parents=True, exist_ok=True)
    for p in photos:
        shutil.copy2(p, scan / "images" / p.name)
    print(f"Processing {len(photos)} photos as '{name}'")

    def show(st):
        print(f"  {st.get('progress', 0) * 100:5.1f}%  {st.get('message', '')}", flush=True)

    try:
        report = Pipeline(scan, cfg, on_status=show).run()
    except Exception as e:  # noqa: BLE001
        print(f"\nFAILED: {e}")
        return 1
    s = report["size_mm"]
    print(f"\nSTL: {scan / report['stl']}\nSize: {s[0]:.2f} x {s[1]:.2f} x {s[2]:.2f} mm")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ScanStation - phone photos to true-scale STL")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start the server for the phone app (default)")
    p = sub.add_parser("process", help="process a folder of photos")
    p.add_argument("folder")
    p.add_argument("--name")
    p.add_argument("--overwrite", action="store_true")
    sub.add_parser("mat", help="write printable mats")
    sub.add_parser("check", help="check installation")
    args = ap.parse_args()
    if args.cmd == "mat":
        cmd_mat(args)
        return 0
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "process":
        return cmd_process(args)
    from scanlib import server

    cfg = config.load()
    for size in mat.MATS:
        p = config.ROOT / f"Scan3D-mat-{size}.pdf"
        if not p.exists():
            mat.make_pdf(str(p), size)
    server.serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
