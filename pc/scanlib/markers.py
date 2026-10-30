"""True-scale alignment: find the mat markers in the photos, triangulate their
corners with the camera poses COLMAP solved, and fit the similarity transform
that maps COLMAP's arbitrary units onto millimetres on the mat.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from . import mat


# ---------------------------------------------------------------- COLMAP text model


@dataclass
class Camera:
    model: str
    width: int
    height: int
    K: np.ndarray
    dist: np.ndarray


@dataclass
class Image:
    image_id: int
    name: str
    camera_id: int
    R: np.ndarray  # cam_from_world rotation
    t: np.ndarray  # cam_from_world translation

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t


def _quat_to_R(qw, qx, qy, qz) -> np.ndarray:
    q = np.array([qw, qx, qy, qz], dtype=np.float64)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _camera_from_params(model: str, w: int, h: int, p: list[float]) -> Camera:
    z = 0.0
    if model == "SIMPLE_PINHOLE":
        f, cx, cy = p
        fx = fy = f
        dist = [z, z, z, z]
    elif model == "PINHOLE":
        fx, fy, cx, cy = p
        dist = [z, z, z, z]
    elif model == "SIMPLE_RADIAL":
        f, cx, cy, k = p
        fx = fy = f
        dist = [k, z, z, z]
    elif model == "RADIAL":
        f, cx, cy, k1, k2 = p
        fx = fy = f
        dist = [k1, k2, z, z]
    elif model == "OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2 = p
        dist = [k1, k2, p1, p2]
    elif model == "FULL_OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6 = p
        dist = [k1, k2, p1, p2, k3, k4, k5, k6]
    else:
        raise ValueError(f"Unsupported camera model {model}")
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
    return Camera(model, w, h, K, np.array(dist, dtype=np.float64))


def read_text_model(path: str) -> tuple[dict[int, Camera], dict[int, Image]]:
    cams: dict[int, Camera] = {}
    with open(os.path.join(path, "cameras.txt")) as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            el = line.split()
            cams[int(el[0])] = _camera_from_params(el[1], int(el[2]), int(el[3]), [float(v) for v in el[4:]])
    imgs: dict[int, Image] = {}
    with open(os.path.join(path, "images.txt")) as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    # two lines per image; the second (2D points) may be empty
    for i in range(0, len(lines) - 1, 2):
        el = lines[i].split()
        if len(el) < 10:
            continue
        qw, qx, qy, qz, tx, ty, tz = (float(v) for v in el[1:8])
        imgs[int(el[0])] = Image(
            int(el[0]), " ".join(el[9:]), int(el[8]), _quat_to_R(qw, qx, qy, qz), np.array([tx, ty, tz])
        )
    return cams, imgs


# ---------------------------------------------------------------- detection


def detect(image_path: str) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Detect mat markers. Returns (ids[N], corners[N,4,2], (w,h)).

    The image is read WITHOUT applying EXIF rotation, because COLMAP works on
    the raw stored pixels, and corner coordinates must be in that same frame.
    """
    import cv2

    data = np.fromfile(image_path, dtype=np.uint8)  # works with non-ASCII Windows paths
    gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
    if gray is None:
        raise IOError(f"Cannot read {image_path}")
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = max(3, int(round(min(gray.shape) / 600)))
    params.cornerRefinementMaxIterations = 50
    params.cornerRefinementMinAccuracy = 0.01
    params.minMarkerPerimeterRate = 0.02
    detector = cv2.aruco.ArucoDetector(mat.aruco_dictionary(), params)
    corners, ids, _ = detector.detectMarkers(gray)
    h, w = gray.shape
    if ids is None or len(ids) == 0:
        return np.zeros((0,), int), np.zeros((0, 4, 2)), (w, h)
    return ids.reshape(-1).astype(int), np.array(corners, dtype=np.float64).reshape(-1, 4, 2), (w, h)


# ---------------------------------------------------------------- geometry


def _normalize(cam: Camera, px: np.ndarray) -> np.ndarray:
    """Pixel coords (OpenCV convention) -> undistorted normalized coords.

    OpenCV puts pixel centres on integers; COLMAP on .5. COLMAP's principal
    point is in its own convention, so shift by +0.5 first.
    """
    import cv2

    pts = (px.reshape(-1, 1, 2) + 0.5).astype(np.float64)
    return cv2.undistortPoints(pts, cam.K, cam.dist).reshape(-1, 2)


def triangulate(views: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]]):
    """views: list of (R, t, normalized_xy, focal_px). Returns (X, per-view px errors)."""
    A = []
    for R, t, xn, _ in views:
        P = np.hstack([R, t[:, None]])
        A.append(xn[0] * P[2] - P[0])
        A.append(xn[1] * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    Xh = Vt[-1]
    X = Xh[:3] / Xh[3]
    # Gauss-Newton refinement of reprojection error (in pixels)
    for _ in range(15):
        J, r = [], []
        for R, t, xn, f in views:
            Xc = R @ X + t
            u = Xc[:2] / Xc[2]
            r.append((u - xn) * f)
            J.append(f * (np.hstack([np.eye(2), -u[:, None]]) / Xc[2]) @ R)
        J = np.vstack(J)
        r = np.concatenate(r)
        dx = np.linalg.lstsq(J, -r, rcond=None)[0]
        X = X + dx
        if np.linalg.norm(dx) < 1e-10 * (1 + np.linalg.norm(X)):
            break
    errs = []
    for R, t, xn, f in views:
        Xc = R @ X + t
        errs.append(np.linalg.norm(Xc[:2] / Xc[2] - xn) * f if Xc[2] > 0 else np.inf)
    return X, np.array(errs)


def umeyama(src: np.ndarray, dst: np.ndarray):
    """Least-squares similarity: dst ~= s * R @ src + t."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    C = D.T @ S / len(src)
    U, sig, Vt = np.linalg.svd(C)
    E = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        E[2, 2] = -1
    R = U @ E @ Vt
    s = np.trace(np.diag(sig) @ E) / ((S ** 2).sum() / len(src))
    t = mu_d - s * R @ mu_s
    return s, R, t


class ScaleError(RuntimeError):
    pass


def align(
    model_dir: str,
    image_dir: str,
    print_correction: dict | None = None,
    progress=None,
    log=print,
) -> dict:
    """Compute the COLMAP -> mat-millimetre similarity transform."""
    cams, imgs = read_text_model(model_dir)
    if not imgs:
        raise ScaleError("The photo alignment produced no cameras.")

    # 1) detect markers in every registered photo
    obs: dict[tuple[int, int], list] = {}  # (marker_id, corner) -> [(image_id, xy_px)]
    all_ids: list[int] = []
    n_with = 0
    items = sorted(imgs.values(), key=lambda im: im.name)
    for k, im in enumerate(items):
        if progress:
            progress(k / len(items))
        ids, corners, (w, h) = detect(os.path.join(image_dir, im.name))
        cam = cams[im.camera_id]
        if (w, h) != (cam.width, cam.height):
            raise ScaleError(
                f"{im.name} is {w}x{h} but COLMAP used {cam.width}x{cam.height}. "
                "Mixed photo orientations/resolutions are not supported."
            )
        if len(ids):
            n_with += 1
        for mid, c4 in zip(ids, corners):
            all_ids.append(int(mid))
            for ci in range(4):
                obs.setdefault((int(mid), ci), []).append((im.image_id, c4[ci]))
    log(f"Mat markers found in {n_with} of {len(items)} aligned photos")
    mat_name = mat.which_mat(all_ids)
    if mat_name is None or n_with < 3:
        raise ScaleError(
            "The scanning mat wasn't visible in enough photos. Keep several mat markers "
            "in frame in most shots (step back a little or tilt down more)."
        )
    scale_x, scale_y = (print_correction or {}).get(mat_name, (1.0, 1.0))
    if (scale_x, scale_y) != (1.0, 1.0):
        log(f"Printer correction for {mat_name} mat: x {scale_x:.5f}, y {scale_y:.5f}")
    board = mat.board_points(mat_name, scale_x, scale_y)

    # 2) triangulate each marker corner
    src, dst, keys, reproj = [], [], [], []
    for (mid, ci), lst in obs.items():
        if mid not in board or len(lst) < 2:
            continue
        views = []
        for image_id, xy in lst:
            im = imgs[image_id]
            cam = cams[im.camera_id]
            xn = _normalize(cam, xy)[0]
            views.append((im.R, im.t, xn, float(cam.K[0, 0])))
        for _ in range(3):  # drop outlier views, re-solve
            if len(views) < 2:
                break
            X, err = triangulate(views)
            bad = err > max(2.0, 3.0 * np.median(err))
            if not bad.any():
                break
            views = [v for v, b in zip(views, bad) if not b]
        if len(views) < 2:
            continue
        X, err = triangulate(views)
        # require a reasonable triangulation angle
        centers = [-(v[0].T @ v[1]) for v in views]
        rays = [(c - X) / np.linalg.norm(c - X) for c in centers]
        max_ang = max(
            np.degrees(np.arccos(np.clip(rays[i] @ rays[j], -1, 1)))
            for i in range(len(rays))
            for j in range(i + 1, len(rays))
        )
        if max_ang < 4.0 or not np.isfinite(err).all():
            continue
        src.append(X)
        dst.append(board[mid][ci])
        keys.append((mid, ci))
        reproj.append(float(np.sqrt(np.mean(err ** 2))))
    src, dst = np.array(src), np.array(dst)
    if len(src) < 8:
        raise ScaleError(
            f"Only {len(src)} mat points could be measured; at least 8 are needed. "
            "Make sure the mat is well lit, flat and in frame from several angles."
        )

    # 3) robust similarity fit
    keep = np.ones(len(src), bool)
    for _ in range(5):
        s, R, t = umeyama(src[keep], dst[keep])
        res = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
        med = np.median(res[keep])
        new_keep = res < max(0.6, 4.0 * 1.4826 * np.median(np.abs(res[keep] - med)) + med)
        if (new_keep == keep).all():
            break
        keep = new_keep
    s, R, t = umeyama(src[keep], dst[keep])
    res = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
    rms = float(np.sqrt(np.mean(res[keep] ** 2)))

    # cameras must be above the mat; if not, the mat was printed mirrored
    cz = np.array([(s * R @ im.center + t)[2] for im in imgs.values()])
    if np.median(cz) < 0:
        raise ScaleError("The cameras ended up below the mat - was the mat printed mirrored?")

    n_markers = len({k[0] for k, ok in zip(keys, keep) if ok})
    log(
        f"Scale fit on mat {mat_name}: {int(keep.sum())} corners from {n_markers} markers, "
        f"RMS {rms:.3f} mm, max {res[keep].max():.3f} mm, reprojection {np.median(reproj):.2f} px"
    )
    return dict(
        mat=mat_name,
        print_correction=[float(scale_x), float(scale_y)],
        scale=float(s),
        R=R.tolist(),
        t=t.tolist(),
        rms_mm=rms,
        max_mm=float(res[keep].max()),
        points_used=int(keep.sum()),
        points_total=int(len(src)),
        markers_used=n_markers,
        photos_with_markers=n_with,
        median_reproj_px=float(np.median(reproj)),
        camera_heights_mm=[float(np.min(cz)), float(np.max(cz))],
    )


def apply(transform: dict, pts: np.ndarray) -> np.ndarray:
    R = np.asarray(transform["R"])
    return (transform["scale"] * (R @ pts.T)).T + np.asarray(transform["t"])
