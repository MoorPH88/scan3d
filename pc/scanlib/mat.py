"""The printed scanning mat: marker layout (in millimetres) and PDF generation.

Mat frame: origin at the centre of the page, X to the right, Y towards the top
of the printed page, Z up out of the paper. All marker corners lie on Z = 0.
"""
from __future__ import annotations

import numpy as np

ARUCO_DICT = "DICT_5X5_250"
CELLS = 7  # 5x5 data bits + 1-cell black border on each side

# A4 landscape design; A3 is the same design scaled by sqrt(2) (A-series pages).
_A4 = dict(
    page=(297.0, 210.0),
    marker=28.0,
    pitch=35.0,
    cols=8,
    rows=5,
    id_offset=0,
    check=(180.0, 100.0),
)
_A3 = dict(
    page=(420.0, 297.0),
    marker=28.0 * 2 ** 0.5,
    pitch=35.0 * 2 ** 0.5,
    cols=8,
    rows=5,
    id_offset=30,
    check=(260.0, 140.0),
)
MATS = {"A4": _A4, "A3": _A3}


def aruco_dictionary():
    import cv2

    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ARUCO_DICT))


def layout(name: str = "A4") -> dict:
    """Return page size, marker corners and check-line geometry for a mat."""
    spec = MATS[name]
    s, p = spec["marker"], spec["pitch"]
    cols, rows = spec["cols"], spec["rows"]
    total_w = cols * s + (cols - 1) * (p - s)
    total_h = rows * s + (rows - 1) * (p - s)
    x_left, y_bottom = -total_w / 2, -total_h / 2

    # Ring of markers: full top row, full bottom row, middle rows at both ends.
    slots = []
    for c in range(cols):
        slots.append((c, rows - 1))
    for r in range(rows - 2, 0, -1):
        slots.append((cols - 1, r))
    for c in range(cols - 1, -1, -1):
        slots.append((c, 0))
    for r in range(1, rows - 1):
        slots.append((0, r))

    markers = {}
    for i, (c, r) in enumerate(slots):
        x0 = x_left + c * p
        y0 = y_bottom + r * p
        # OpenCV corner order: top-left, top-right, bottom-right, bottom-left
        # (as the marker appears upright on the printed page).
        markers[spec["id_offset"] + i] = np.array(
            [[x0, y0 + s], [x0 + s, y0 + s], [x0 + s, y0], [x0, y0]], dtype=np.float64
        )

    inner = (total_w / 2 - s, total_h / 2 - s)  # half-extents of the clear area
    cx, cy = spec["check"]
    return dict(
        name=name,
        page=spec["page"],
        marker=s,
        markers=markers,
        inner_half=inner,
        outer_half=(total_w / 2, total_h / 2),
        check_x=((-cx / 2, -inner[1] + 6.0), (cx / 2, -inner[1] + 6.0)),
        check_y=((inner[0] - 6.0, -cy / 2), (inner[0] - 6.0, cy / 2)),
        check_len=(cx, cy),
    )


def which_mat(ids) -> str | None:
    """Pick the mat whose marker ids best match the detected ids."""
    ids = set(int(i) for i in ids)
    best, best_n = None, 0
    for name in MATS:
        n = len(ids & set(layout(name)["markers"].keys()))
        if n > best_n:
            best, best_n = name, n
    return best


def board_points(name: str, scale_x: float = 1.0, scale_y: float = 1.0) -> dict:
    """Marker corners in mm (3D, z=0), optionally corrected for printer scaling."""
    out = {}
    for mid, c in layout(name)["markers"].items():
        pts = np.zeros((4, 3))
        pts[:, 0] = c[:, 0] * scale_x
        pts[:, 1] = c[:, 1] * scale_y
        out[mid] = pts
    return out


def make_pdf(path: str, name: str = "A4") -> None:
    """Write a vector PDF of the mat at exact physical size."""
    import cv2
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    L = layout(name)
    W, H = L["page"]
    c = canvas.Canvas(path, pagesize=(W * mm, H * mm))
    c.setTitle(f"Scan3D scanning mat ({name})")

    def P(x, y):  # mat mm -> PDF points (PDF origin is bottom-left, Y up)
        return (x + W / 2) * mm, (y + H / 2) * mm

    # Light random texture inside the clear area helps photo alignment when
    # few markers are in view. Kept away from the markers' white quiet zone.
    rng = np.random.default_rng(1234)
    ix, iy = L["inner_half"]
    q = L["marker"] / CELLS * 1.5
    c.setFillGray(0.62)
    for _ in range(int(ix * iy * 0.09)):
        x = rng.uniform(-ix + q, ix - q)
        y = rng.uniform(-iy + q, iy - q)
        r = rng.uniform(0.5, 1.6)
        if abs(x) < 3 and abs(y) < 3:
            continue
        if abs(y - L["check_x"][0][1]) < 4 or abs(x - L["check_y"][0][0]) < 4:
            continue  # keep the check lines easy to read
        px, py = P(x, y)
        if rng.random() < 0.5:
            c.circle(px, py, r * mm, stroke=0, fill=1)
        else:
            c.rect(px - r * mm, py - r * mm, 2 * r * mm, 2 * r * mm, stroke=0, fill=1)

    # Markers
    dictionary = aruco_dictionary()
    cell = L["marker"] / CELLS
    c.setFillGray(0.0)
    for mid, corners in L["markers"].items():
        img = cv2.aruco.generateImageMarker(dictionary, int(mid), CELLS, borderBits=1)
        x0, y_top = corners[0]
        for r in range(CELLS):
            for k in range(CELLS):
                if img[r, k] < 128:
                    px, py = P(x0 + k * cell, y_top - (r + 1) * cell)
                    # tiny overlap avoids hairline gaps between cells in some viewers
                    c.rect(px, py, cell * mm + 0.05, cell * mm + 0.05, stroke=0, fill=1)

    # Centre target
    c.setStrokeGray(0.0)
    c.setLineWidth(0.3 * mm)
    for a, b in (((-6, 0), (6, 0)), ((0, -6), (0, 6))):
        c.line(*P(*a), *P(*b))
    c.setDash(3 * mm, 2 * mm)
    c.setLineWidth(0.25 * mm)
    c.circle(*P(0, 0), 40 * mm, stroke=1, fill=0)
    c.setDash()

    # Check lines with crosshair ends
    c.setFillGray(0.0)
    c.setFont("Helvetica", 7)
    for (a, b), length, label in (
        (L["check_x"], L["check_len"][0], "A"),
        (L["check_y"], L["check_len"][1], "B"),
    ):
        c.setLineWidth(0.15 * mm)
        c.line(*P(*a), *P(*b))
        for e in (a, b):
            ex, ey = e
            c.line(*P(ex - 3, ey), *P(ex + 3, ey))
            c.line(*P(ex, ey - 3), *P(ex, ey + 3))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if label == "A":
            c.drawCentredString(*P(mx, my + 1.5), f"Line A = {length:.1f} mm (centre to centre of crosses)")
        else:
            px, py = P(mx - 2.5, my)
            c.saveState()
            c.translate(px, py)
            c.rotate(90)
            c.drawCentredString(0, 0, f"Line B = {length:.1f} mm")
            c.restoreState()

    # Margin text
    oy = L["outer_half"][1]
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(*P(0, oy + 5), f"Scan3D scanning mat {name}  -  print at 100% / 'Actual size' (never 'Fit to page')")
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(
        *P(0, -oy - 7),
        "Measure lines A and B with a steel rule or calipers. If they are not exact, enter your values in ScanStation's config.json.",
    )
    c.drawCentredString(
        *P(0, -oy - 11.5),
        "Keep the mat flat (glue it to card or foam board), matte and uncreased. Place the item on the centre cross.",
    )
    c.showPage()
    c.save()
