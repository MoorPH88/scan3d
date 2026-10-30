from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("SCANSTATION_CONFIG") or ROOT / "config.json")

DEFAULTS = {
    "port": 8765,
    "discovery_port": 8766,
    "pairing_code": "",
    "scans_dir": "scans",
    "colmap_path": "",
    # Measured lengths of check lines A and B on your printed mat (mm).
    # Leave as printed values unless your ruler/calipers say otherwise.
    "mat_check_mm": {"A4": [180.0, 100.0], "A3": [260.0, 140.0]},
    # Longest side of the images used for dense reconstruction. Higher = finer
    # detail but slower (2400 suits 6-12 GB GPUs; try 3200 on 16 GB+).
    "dense_image_size": 2400,
    "max_triangles": 600000,
    "flat_base": True,
    "keep_work_files": False,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise SystemExit(f"config.json is not valid JSON: {e}")
    changed = not CONFIG_PATH.exists()
    if not cfg.get("pairing_code"):
        cfg["pairing_code"] = f"{secrets.randbelow(10**6):06d}"
        changed = True
    for k, v in DEFAULTS.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def scans_dir(cfg: dict) -> Path:
    p = Path(cfg["scans_dir"])
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p
