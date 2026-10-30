"""Dense points (COLMAP units) -> cleaned, watertight STL in millimetres."""
from __future__ import annotations

import numpy as np

from . import markers


class MeshError(RuntimeError):
    pass


def _rotation_to_z(n: np.ndarray) -> np.ndarray:
    """Rotation taking unit vector n onto +Z."""
    n = n / np.linalg.norm(n)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(n, z)
    c = float(n @ z)
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def build(
    fused_ply: str,
    transform: dict,
    out_stl: str,
    *,
    mat_half: tuple[float, float],
    base_cut_mm: float = 0.5,
    max_height_mm: float = 400.0,
    max_triangles: int = 600_000,
    flat_base: bool = True,
    log=print,
) -> dict:
    import open3d as o3d
    import trimesh

    pcd = o3d.io.read_point_cloud(fused_ply)
    if len(pcd.points) == 0:
        raise MeshError("Dense reconstruction produced no points.")
    pts = markers.apply(transform, np.asarray(pcd.points))
    R = np.asarray(transform["R"])
    has_normals = pcd.has_normals()
    nrm = (R @ np.asarray(pcd.normals).T).T if has_normals else None
    log(f"Dense cloud: {len(pts):,} points")

    # --- 1. snap the mat surface exactly to z = 0 (corrects slight paper curl / fit tilt)
    near = (np.abs(pts[:, 2]) < 2.5) & (np.hypot(pts[:, 0], pts[:, 1]) < min(mat_half) * 1.6)
    plane_rms = None
    if near.sum() > 500:
        sub = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts[near]))
        model, inl = sub.segment_plane(distance_threshold=0.35, ransac_n=3, num_iterations=2000)
        a, b, c, d = model
        n = np.array([a, b, c])
        if n[2] < 0:
            n, d = -n, -d
        norm = np.linalg.norm(n)
        n, d = n / norm, d / norm
        Rz = _rotation_to_z(n)
        # point on plane closest to origin is -d*n
        pts = (Rz @ (pts + d * n).T).T
        if nrm is not None:
            nrm = (Rz @ nrm.T).T
        inlier_pts = pts[near][inl]
        plane_rms = float(np.sqrt(np.mean(inlier_pts[:, 2] ** 2)))
        tilt = float(np.degrees(np.arccos(np.clip(n[2], -1, 1))))
        log(f"Mat plane refit: tilt {tilt:.2f} deg, offset {d:.3f} mm, surface noise {plane_rms:.3f} mm RMS")

    # --- 2. crop to the item sitting on the mat
    if plane_rms is not None:
        base_cut_mm = max(base_cut_mm, 3.0 * plane_rms)
    hx, hy = mat_half
    keep = (
        (pts[:, 2] > base_cut_mm)
        & (pts[:, 2] < max_height_mm)
        & (np.abs(pts[:, 0]) < hx)
        & (np.abs(pts[:, 1]) < hy)
    )
    pts = pts[keep]
    if nrm is not None:
        nrm = nrm[keep]
    if len(pts) < 2000:
        raise MeshError(
            "Hardly any points were found above the mat. Is the item inside the marker ring "
            "and does it have some visible texture?"
        )
    obj = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    if nrm is not None:
        obj.normals = o3d.utility.Vector3dVector(nrm)

    obj = obj.voxel_down_sample(0.15)
    obj, _ = obj.remove_statistical_outlier(nb_neighbors=24, std_ratio=2.0)

    # --- 3. keep the item, drop stray blobs (hands, cables, background)
    coarse = obj.voxel_down_sample(1.0)
    labels = np.array(coarse.cluster_dbscan(eps=3.0, min_points=8))
    if labels.max() < 0:
        raise MeshError("Could not separate the item from noise.")
    counts = np.bincount(labels[labels >= 0])
    good = np.flatnonzero(counts >= max(30, 0.03 * counts.max()))
    cpts = np.asarray(coarse.points)[np.isin(labels, good)]
    from scipy.spatial import cKDTree

    d, _ = cKDTree(cpts).query(np.asarray(obj.points), k=1)
    obj = obj.select_by_index(np.flatnonzero(d < 1.5))
    P = np.asarray(obj.points)
    log(f"Item points after cleaning: {len(P):,}")

    # --- 4. normals (COLMAP's fused normals point at the cameras = outwards)
    if not obj.has_normals():
        obj.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=1.5, max_nn=40))
        obj.orient_normals_towards_camera_location(np.array([0.0, 0.0, 2000.0]))
    else:
        nn = np.asarray(obj.normals)
        nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)
        obj.normals = o3d.utility.Vector3dVector(nn)

    # --- 5. close the unseen underside with a flat floor at z = 0
    if flat_base:
        from scipy import ndimage

        low = P[P[:, 2] < base_cut_mm + 4.0]
        if len(low) > 50:
            cell = 0.5
            mn = low[:, :2].min(0) - 3
            ij = np.floor((low[:, :2] - mn) / cell).astype(int)
            grid = np.zeros(ij.max(0) + 8, bool)
            grid[ij[:, 0], ij[:, 1]] = True
            grid = ndimage.binary_closing(grid, structure=np.ones((5, 5)), iterations=2)
            grid = ndimage.binary_fill_holes(grid)
            grid = ndimage.binary_erosion(grid, iterations=1)
            ii, jj = np.nonzero(grid)
            floor = np.column_stack([mn[0] + (ii + 0.5) * cell, mn[1] + (jj + 0.5) * cell, np.zeros(len(ii))])
            fl = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(floor))
            fl.normals = o3d.utility.Vector3dVector(np.tile([0.0, 0.0, -1.0], (len(floor), 1)))
            obj = obj + fl
            log(f"Base footprint: {grid.sum() * cell * cell / 100:.1f} cm2")

    # --- 6. Poisson surface
    ext = np.ptp(np.asarray(obj.points), axis=0).max()
    depth = int(np.clip(np.ceil(np.log2(ext * 1.1 / 0.25)), 8, 11))
    log(f"Poisson meshing (depth {depth}, item extent {ext:.1f} mm)")
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(obj, depth=depth, scale=1.1, n_threads=-1)
    mesh.remove_duplicated_vertices()
    mesh.remove_degenerate_triangles()
    if len(mesh.triangles) > max_triangles:
        mesh = mesh.simplify_quadric_decimation(max_triangles)
        mesh.remove_degenerate_triangles()
        mesh.remove_unreferenced_vertices()

    tm = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.triangles), process=True)

    # --- 7. flat, capped base exactly on z = 0
    if flat_base and tm.bounds[0, 2] < 0:
        try:
            tm = tm.slice_plane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], cap=True)
        except Exception as e:  # capping needs mapbox_earcut/shapely
            log(f"Warning: could not cap the base ({e}); base left rounded")

    # keep the main body (Poisson sometimes adds tiny islands)
    parts = tm.split(only_watertight=False)
    if len(parts) > 1:
        areas = np.array([p.area for p in parts])
        tm = trimesh.util.concatenate([p for p, a in zip(parts, areas) if a > 0.02 * areas.max()])
    if not tm.is_watertight:
        trimesh.repair.fill_holes(tm)
    trimesh.repair.fix_normals(tm)
    if tm.is_watertight and tm.volume < 0:
        tm.invert()

    # centre on X/Y, base on Z=0 (slicers can then drop it straight on the bed)
    b = tm.bounds
    tm.apply_translation([-(b[0, 0] + b[1, 0]) / 2, -(b[0, 1] + b[1, 1]) / 2, -b[0, 2]])
    tm.export(out_stl)  # binary STL, units = mm

    size = (tm.bounds[1] - tm.bounds[0]).tolist()
    info = dict(
        size_mm=[round(v, 2) for v in size],
        triangles=int(len(tm.faces)),
        watertight=bool(tm.is_watertight),
        volume_cm3=round(float(tm.volume) / 1000.0, 2) if tm.is_watertight else None,
        surface_noise_mm=round(plane_rms, 3) if plane_rms is not None else None,
        poisson_depth=depth,
    )
    log(
        f"STL: {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm, {info['triangles']:,} triangles, "
        f"watertight={info['watertight']}"
    )
    return info


def render_preview(stl_path: str, png_path: str, max_faces: int = 25_000) -> None:
    """Small shaded preview image for the phone."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import trimesh
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    import open3d as o3d

    m = o3d.io.read_triangle_mesh(stl_path)
    m.remove_duplicated_vertices()  # STL is a triangle soup; weld before decimating
    if len(m.triangles) > max_faces:
        m = m.simplify_quadric_decimation(max_faces)
    m.remove_degenerate_triangles()
    m.compute_triangle_normals()
    V = np.asarray(m.vertices)
    Fc = np.asarray(m.triangles)
    tris = V[Fc]
    n = np.asarray(m.triangle_normals)
    light = np.array([0.35, -0.75, 0.55])
    light /= np.linalg.norm(light)
    shade = np.clip(n @ light, 0, 1) * 0.7 + 0.22
    base = np.array([0.30, 0.55, 0.85])
    colors = np.column_stack([np.outer(shade, base), np.ones_like(shade)])
    tm = trimesh.Trimesh(V, Fc, process=False)
    elev, azim = 22.0, -60.0
    e, a = np.radians(elev), np.radians(azim)
    eye = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    front = n @ eye > 0  # back-face culling keeps the painter's sort honest
    tris, colors = tris[front], colors[front]

    fig = plt.figure(figsize=(6, 6), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    pc = Poly3DCollection(tris, facecolors=colors, edgecolors=colors, linewidths=0.4, antialiased=False)
    ax.add_collection3d(pc)
    b = tm.bounds
    c = (b[0] + b[1]) / 2
    r = (b[1] - b[0]).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    s = b[1] - b[0]
    fig.text(0.5, 0.04, f"{s[0]:.1f} x {s[1]:.1f} x {s[2]:.1f} mm", ha="center", fontsize=13)
    fig.savefig(png_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
