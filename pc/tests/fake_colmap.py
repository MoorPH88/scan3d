#!/usr/bin/env python3
"""Stand-in for the COLMAP CLI so the pipeline can be tested without a GPU:
sparse steps run through pycolmap (CPU); dense steps synthesise the box cloud."""
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

cmd, rest = sys.argv[1], sys.argv[2:]
a = {rest[i].lstrip("-"): rest[i + 1] for i in range(0, len(rest) - 1, 2)}

if cmd == "help":
    print("COLMAP fake for tests")
    sys.exit(0)

import pycolmap  # noqa: E402

if cmd == "feature_extractor":
    ro = pycolmap.ImageReaderOptions()
    ro.camera_model = a.get("ImageReader.camera_model", "SIMPLE_RADIAL")
    n = len(list(Path(a["image_path"]).glob("*.jpg")))
    pycolmap.extract_features(a["database_path"], a["image_path"], camera_mode=pycolmap.CameraMode.SINGLE, reader_options=ro)
    for i in range(1, n + 1):
        print(f"Processed file [{i}/{n}]")
elif cmd == "exhaustive_matcher":
    print("Processing block [1/1, 1/1]")
    pycolmap.match_exhaustive(a["database_path"])
elif cmd == "mapper":
    recs = pycolmap.incremental_mapping(a["database_path"], a["image_path"], a["output_path"])
    for k, r in recs.items():
        print(f"model {k}: num_reg_frames={r.num_reg_images()}")
elif cmd == "model_converter":
    pycolmap.Reconstruction(a["input_path"]).write_text(a["output_path"])
elif cmd == "image_undistorter":
    Path(a["output_path"]).mkdir(parents=True, exist_ok=True)
elif cmd == "patch_match_stereo":
    for i in range(1, 11):
        print(f"Processing view {i} / 10 for IMG_{i:04d}.jpg")
elif cmd == "stereo_fusion":
    import json

    import open3d as o3d
    from mesh_test import sample_box

    T = json.loads((Path(a["workspace_path"]).parent / "transform.json").read_text())
    P, N = sample_box()
    rng = np.random.default_rng(1)
    m = rng.uniform([-105, -54], [105, 54], (200_000, 2))
    m = m[(np.abs(m[:, 0]) > 40) | (np.abs(m[:, 1]) > 40)]
    M = np.column_stack([m, rng.normal(0, 0.1, len(m))])
    X = np.vstack([P, M])
    XN = np.vstack([N, np.tile([0, 0, 1.0], (len(M), 1))])
    R, t, s = np.array(T["R"]), np.array(T["t"]), T["scale"]
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(((X - t) @ R) / s))
    pcd.normals = o3d.utility.Vector3dVector(XN @ R)
    o3d.io.write_point_cloud(a["output_path"], pcd)
    print("Fusing image [10/10] with index 9")
else:
    print(f"fake colmap: unknown command {cmd}", file=sys.stderr)
    sys.exit(2)
