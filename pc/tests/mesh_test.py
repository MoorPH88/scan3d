"""Mesh-stage test: a noisy synthetic 'fused.ply' of the box in COLMAP units
(using the transform from tests/synth.py) -> STL, compared with the true size.

    python tests/mesh_test.py SYNTH_OUT_DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scanlib import mat, mesh  # noqa: E402
from synth import BOX, BOX_YAW  # noqa: E402

rng = np.random.default_rng(3)


def sample_box(step=0.3, sigma=0.12):
    w, d, h = BOX
    pts, nrm = [], []

    def face(origin, u, v, n, lu, lv):
        a, b = np.meshgrid(np.arange(0, lu, step), np.arange(0, lv, step))
        p = origin + a.ravel()[:, None] * u + b.ravel()[:, None] * v
        pts.append(p + rng.normal(0, sigma, (len(p), 1)) * n)
        nrm.append(np.tile(n, (len(p), 1)))

    face(np.array([-w / 2, -d / 2, h]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), w, d)
    face(np.array([-w / 2, -d / 2, 0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), np.array([0, -1.0, 0]), w, h)
    face(np.array([-w / 2, d / 2, 0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), np.array([0, 1.0, 0]), w, h)
    face(np.array([-w / 2, -d / 2, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), np.array([-1.0, 0, 0]), d, h)
    face(np.array([w / 2, -d / 2, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), np.array([1.0, 0, 0]), d, h)
    P, N = np.vstack(pts), np.vstack(nrm)
    c, s = np.cos(BOX_YAW), np.sin(BOX_YAW)
    Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    return P @ Rz.T, N @ Rz.T


def main(synth_dir: Path):
    T = json.loads((synth_dir / "transform.json").read_text())
    P, N = sample_box()
    # mat surface around the item (with paper-like noise), minus the footprint
    m = rng.uniform([-105, -54], [105, 54], (400_000, 2))
    c, s = np.cos(-BOX_YAW), np.sin(-BOX_YAW)
    local = m @ np.array([[c, -s], [s, c]]).T
    out = (np.abs(local[:, 0]) > BOX[0] / 2) | (np.abs(local[:, 1]) > BOX[1] / 2)
    m = m[out]
    M = np.column_stack([m, rng.normal(0, 0.1, len(m)) + 0.0006 * m[:, 0]])  # slight tilt
    MN = np.tile([0, 0, 1.0], (len(M), 1))
    # junk: a few floating blobs and scattered noise
    J = np.vstack([rng.normal([80, 40, 30], 2, (300, 3)), rng.uniform([-100, -50, 0], [100, 50, 150], (500, 3))])
    JN = rng.normal(0, 1, J.shape)
    JN /= np.linalg.norm(JN, axis=1, keepdims=True)
    X = np.vstack([P, M, J])
    XN = np.vstack([N, MN, JN])

    # into COLMAP units: x_c = R^T (x_mm - t) / s
    R, t, sc = np.array(T["R"]), np.array(T["t"]), T["scale"]
    Xc = ((X - t) @ R) / sc
    Nc = XN @ R
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Xc))
    pcd.normals = o3d.utility.Vector3dVector(Nc)
    ply = synth_dir / "fused_synth.ply"
    o3d.io.write_point_cloud(str(ply), pcd)

    stl = synth_dir / "box.stl"
    info = mesh.build(str(ply), T, str(stl), mat_half=mat.layout("A4")["inner_half"])
    mesh.render_preview(str(stl), str(synth_dir / "box_preview.png"))
    w, d, h = BOX
    ca, sa = abs(np.cos(BOX_YAW)), abs(np.sin(BOX_YAW))
    expect = [w * ca + d * sa, w * sa + d * ca, h]
    err = np.array(info["size_mm"]) - np.array(expect)
    print("expected", np.round(expect, 2), "got", info["size_mm"], "err", np.round(err, 3))
    print(json.dumps(info))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
