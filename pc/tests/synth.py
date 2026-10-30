"""Synthetic end-to-end accuracy test.

Renders photos of a textured box (known size) on the A4 mat from a phone-like
camera with lens distortion, runs COLMAP's sparse reconstruction (pycolmap, CPU),
then the ScanStation true-scale alignment, and reports the error in millimetres.

    python tests/synth.py OUT_DIR
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scanlib import markers, mat  # noqa: E402

RNG = np.random.default_rng(7)
W, H = 2000, 1500
F = 1650.0
K = np.array([[F, 0, W / 2], [0, F, H / 2], [0, 0, 1.0]])
DIST = np.array([-0.06, 0.02, 0.0005, -0.0003])
BOX = (60.0, 40.0, 90.0)  # width (x), depth (y), height (z) in mm
BOX_YAW = np.radians(20)


def texture(h, w, seed):
    r = np.random.default_rng(seed)
    img = np.zeros((h, w), np.float32)
    for s in (4, 9, 23, 61):
        n = r.random((max(2, h // s), max(2, w // s))).astype(np.float32)
        img += cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC) * s
    img = (img - img.min()) / (np.ptp(img) + 1e-6)
    col = np.stack([img, np.roll(img, 7, 0), np.roll(img, 13, 1)], -1)
    return (col * 200 + 30).astype(np.uint8)


def mat_texture(tmp: Path):
    pdf = tmp / "mat.pdf"
    mat.make_pdf(str(pdf), "A4")
    subprocess.run(["pdftoppm", "-r", "254", "-png", "-singlefile", str(pdf), str(tmp / "mat")], check=True)
    return cv2.imread(str(tmp / "mat.png"))  # exactly 10 px / mm


def look_at(c, target):
    z = target - c
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])  # cam_from_world
    return R, -R @ c


def plane_homography(R, t, origin, ex, ey, px_per_mm):
    """Homography texture-pixel -> image for a plane origin + u*ex + v*ey (u,v in mm)."""
    M = K @ np.column_stack([R @ ex, R @ ey, R @ origin + t])
    S = np.array([[1 / px_per_mm, 0, 0.5 / px_per_mm], [0, 1 / px_per_mm, 0.5 / px_per_mm], [0, 0, 1]])
    return M @ S


def box_faces():
    w, d, h = BOX
    c, s = np.cos(BOX_YAW), np.sin(BOX_YAW)
    Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    corners = {}
    faces = []
    # (origin, ex, ey) in box frame, texture spans |ex| x |ey|
    defs = [
        ((-w / 2, -d / 2, h), (w, 0, 0), (0, d, 0)),  # top
        ((-w / 2, -d / 2, h), (w, 0, 0), (0, 0, -h)),  # front (y=-d/2)
        ((w / 2, d / 2, h), (-w, 0, 0), (0, 0, -h)),  # back
        ((w / 2, -d / 2, h), (0, d, 0), (0, 0, -h)),  # right
        ((-w / 2, d / 2, h), (0, -d, 0), (0, 0, -h)),  # left
    ]
    for i, (o, ex, ey) in enumerate(defs):
        o, ex, ey = (Rz @ np.array(v, float) for v in (o, ex, ey))
        n = np.cross(ex, ey)
        n /= np.linalg.norm(n)
        if n @ (o + ex / 2 + ey / 2 - np.array([0, 0, h / 2])) < 0:
            n = -n  # outward
        faces.append((o, ex, ey, n, texture(int(np.linalg.norm(ey) * 8), int(np.linalg.norm(ex) * 8), 100 + i)))
    return faces


_MAP = None


def _distortion_map():
    global _MAP
    if _MAP is None:
        gx, gy = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
        pts = np.stack([gx.ravel(), gy.ravel()], -1).reshape(-1, 1, 2)
        crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-9)
        try:
            und = cv2.undistortPoints(pts, K, DIST, None, K, criteria=crit)
        except TypeError:
            und = cv2.undistortPoints(pts, K, DIST, None, K)
        _MAP = und.reshape(H, W, 2).astype(np.float32)
    return _MAP


def render(R, t, mat_img, ground, faces):
    img = np.full((H, W, 3), 128, np.uint8)
    # ground (1 m square, 2 px/mm) then mat (10 px/mm), then box faces
    Hg = plane_homography(R, t, np.array([-500.0, 500.0, 0]), np.array([1.0, 0, 0]), np.array([0, -1.0, 0]), 2.0)
    cv2.warpPerspective(ground, Hg, (W, H), dst=img, borderMode=cv2.BORDER_TRANSPARENT, flags=cv2.INTER_LINEAR)
    Hm = plane_homography(R, t, np.array([-148.5, 105.0, 0]), np.array([1.0, 0, 0]), np.array([0, -1.0, 0]), 10.0)
    cv2.warpPerspective(mat_img, Hm, (W, H), dst=img, borderMode=cv2.BORDER_TRANSPARENT, flags=cv2.INTER_AREA)
    cam_c = -R.T @ t
    for o, ex, ey, n, tex in faces:
        center = o + ex / 2 + ey / 2
        if n @ (cam_c - center) <= 0:
            continue
        L = np.linalg.norm(ex)
        Hf = plane_homography(R, t, o, ex / L, ey / np.linalg.norm(ey), 8.0)
        cv2.warpPerspective(tex, Hf, (W, H), dst=img, borderMode=cv2.BORDER_TRANSPARENT, flags=cv2.INTER_LINEAR)
    # lens distortion: output pixel -> undistorted source pixel
    und = _distortion_map()
    out = cv2.remap(img, und[..., 0], und[..., 1], cv2.INTER_LINEAR)
    noise = RNG.normal(0, 2.0, out.shape)
    return np.clip(out + noise, 0, 255).astype(np.uint8)


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    imgdir = out / "images"
    imgdir.mkdir(exist_ok=True)
    mat_img = mat_texture(out)
    ground = texture(1000 * 2, 1000 * 2, 5)
    faces = box_faces()
    cams = {}
    k = 0
    for elev, n in ((12, 20), (32, 20), (58, 12)):
        for a in range(n):
            az = np.radians(a * 360 / n + RNG.uniform(-4, 4))
            r = 330 + RNG.uniform(-25, 25)
            e = np.radians(elev + RNG.uniform(-3, 3))
            c = np.array([r * np.cos(e) * np.cos(az), r * np.cos(e) * np.sin(az), r * np.sin(e)])
            R, t = look_at(c, np.array([RNG.uniform(-8, 8), RNG.uniform(-8, 8), 30 + RNG.uniform(-8, 8)]))
            name = f"IMG_{k:04d}.jpg"
            cv2.imwrite(str(imgdir / name), render(R, t, mat_img, ground, faces), [cv2.IMWRITE_JPEG_QUALITY, 93])
            cams[name] = c
            k += 1
    print(f"rendered {k} images")

    import pycolmap

    db = out / "database.db"
    if db.exists():
        db.unlink()
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "OPENCV"
    pycolmap.extract_features(db, imgdir, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader)
    pycolmap.match_exhaustive(db)
    sparse = out / "sparse"
    sparse.mkdir(exist_ok=True)
    recs = pycolmap.incremental_mapping(db, imgdir, sparse)
    best = max(recs.values(), key=lambda r: r.num_reg_images())
    txt = out / "txt"
    txt.mkdir(exist_ok=True)
    best.write_text(str(txt))
    print(f"registered {best.num_reg_images()} / {k}")

    T = markers.align(str(txt), str(imgdir))
    _, imgs = markers.read_text_model(str(txt))
    errs = []
    for im in imgs.values():
        est = markers.apply(T, im.center[None])[0]
        errs.append(np.linalg.norm(est - cams[im.name]))
    errs = np.array(errs)
    # camera centres ~330 mm away amplify rotation error; the scale error itself:
    gt = np.array([cams[im.name] for im in imgs.values()])
    est = markers.apply(T, np.array([im.center for im in imgs.values()]))
    d_gt = np.linalg.norm(gt[:, None] - gt[None], axis=-1)
    d_est = np.linalg.norm(est[:, None] - est[None], axis=-1)
    mask = d_gt > 50
    scale_err = np.median(d_est[mask] / d_gt[mask]) - 1
    res = dict(
        registered=best.num_reg_images(),
        fit_rms_mm=T["rms_mm"],
        camera_center_err_mm_median=float(np.median(errs)),
        camera_center_err_mm_max=float(errs.max()),
        scale_error_percent=float(scale_err * 100),
        error_on_150mm_item_mm=float(abs(scale_err) * 150),
    )
    print(json.dumps(res, indent=2))
    (out / "transform.json").write_text(json.dumps(T, indent=2))
    (out / "result.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
