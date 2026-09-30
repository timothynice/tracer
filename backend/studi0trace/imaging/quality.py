"""How good a trace is: fidelity to the source and the artifacts a designer sees.

Auto (`studi0trace.auto`) traces an image several ways and keeps the cleanest
of the most faithful, so what the bench measures has to be what the service
measures; this module is both. `bench.artifacts`, `bench.raster` and
`bench.metrics` re-export it.

Fidelity is one flattened render compared with the source: mean CIEDE2000 and
the F1 of the Canny edges matched within 2 px (`delta_e`, `edge_f1`).

The artifact scorecard looks for the defects themselves, from the emitted
geometry and a 4x render, and needs no vector truth. The fidelity metrics
average; a pinhole, a hairline sliver or a wobbling edge moves them by
thousandths.

holes      sub-pixels of a 4x render left uncovered where the source is opaque
           (the backdrop showing through the artwork as a dark tick), and how
           many separate clusters they form; `pinholes` counts the clusters
           deep enough to see (some sub-pixel under half covered).
slivers    closed contours whose area is under SLIVER_AREA px² or whose mean
           thickness 2·area/perimeter is under SLIVER_THICK px, and strokes
           thinner than a pixel; `degenerate` counts contours with no area
           at all (a path that goes out and comes back on itself).
wobble     turning the outline does at a fine scale that it does not do at a
           designer's scale: the total variation of the tangent angle along
           the drawn outline minus the total variation of the chord direction
           over WOBBLE_SCALE px. A line, an arc, a clean corner and a long
           S-curve each turn the same way at both scales (their turning is
           monotone within the window) and score zero; a nick, a hook, a
           zig-zag or a staircase turns back on itself inside the window and
           scores its cancelled turning. Degrees per 100 px of outline.
inflections  a count of the sign changes of the outline's curvature at
           INFLECT_SCALE px, with INFLECT_HYST degrees of hysteresis, inside
           the smooth runs between corners (a line meeting an arc at a corner
           is not a wave): the long, shallow S a sweeping curve or a straight
           edge drawn as one cubic should not have. Legitimate S-curves count
           too, so compare against a baseline rather than against zero.
rects      a contour with exactly four convex corners of 60°–120° and four
           sides that each turn less than RECT_SIDE_DEG and sit on a
           right-angle grid within RECT_GRID is a (rounded) rectangle to a
           designer. A corner is found as CORNER_WINDOW px of outline turning
           that far, and then takes in the whole of the curve it sits on, so
           a radius wider than the window is one corner and not a corner with
           a bent side. Each corner gets a radius (the length over which it
           turns / the angle it turns; a chamfer turns in two spikes and reads
           as sharp). `radius_inconsistent`: radii spread more than
           RADIUS_SPREAD px, or rounded (≥ ROUND_R) mixed with sharp
           (< SHARP_R) — three round corners and a chamfer. `rect_bowed`: a
           side bows more than RECT_BOW px off its own line (a pillow).
           `rect_skewed`: sides off parallel/perpendicular by 1°–6°.

Only what can be seen is scored. An engine that tiles its shapes paints the
earlier of two neighbours a little under the later one (the bleed), and that
copy of the edge, with the jog it makes near a junction, is part of the
earlier shape's path but is never on screen. So the drawing is rendered once
more, without anti-aliasing and with every opaque element in a colour of its
own (`id_map`), and a sample of an outline counts only where it is not
painted over from both sides by later elements (`VIS_OFFSET` px either side
of it). Wobble, inflections, rect measures, slivers and thin strokes are
taken over the visible samples only; `degenerate` is not, because a subpath
with no area is junk in the file wherever it lies.

Every length is in source pixels (a `<g transform="scale(s)">` root, the
small-input upsampler's, is honoured). Every function here is pure.
"""
from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
import resvg_py
from PIL import Image
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.feature import canny
from skimage.morphology import dilation, disk

STEP = 0.25            # resampling step along every outline, px
HOLE_SCALE = 4         # render scale for the hole count
HOLE_COVER = 0.95      # a sub-pixel covered less than this (where the source is opaque) is a hole
PINHOLE_COVER = 0.5    # a hole cluster with a sub-pixel under this is visible as a tick
SLIVER_AREA = 4.0      # px²
SLIVER_THICK = 1.0     # px, 2·area/perimeter
THIN_STROKE = 1.0      # px stroke width
WOBBLE_SCALE = 4.0     # px chord for the designer-scale tangent
INFLECT_SCALE = 6.0    # px chord for the curvature sign test
INFLECT_HYST = 3.0     # degrees of turning before a sign change counts
CORNER_SNAP = 20.0     # degrees turned within 2 px: a corner, where inflections do not count
CORNER_GUARD = 3.0     # px cut out either side of a corner
CORNER_WINDOW = 10.0   # px window a corner's turning must reach CORNER_SEED_DEG within
CORNER_SEED_DEG = 45.0  # a corner is looked for where the window turns this far (a radius up to ~12.7 px)
GROW_MAX = 40.0        # px a corner grows over the curve it sits on, at most
CORNER_MIN_DEG = 60.0  # and it has to turn this far in all
CORNER_MAX_DEG = 120.0
CURVED = 0.04          # rad/px: a sample turning faster than this is part of a corner's curve
RADIUS_SPREAD = 1.0    # px
ROUND_R = 1.5
SHARP_R = 0.75
RECT_BOW = 0.25        # px a rect-like contour's side may bow off its line
RECT_SKEW = 1.0        # degrees its sides may be off parallel / perpendicular
RECT_SIDE_DEG = 30.0   # a side turning more than this is not a side
RECT_GRID = 6.0        # sides further than this off a right-angle grid make a trapezoid, not a rect
ID_SCALE = 2           # render scale of the element-id map (4x reads the same to ~1% at 3x the cost)
VIS_OFFSET = 0.35      # px either side of an outline sample that must both be painted over to hide it
COVER_ALPHA = 0.5      # an element this opaque (opacity × fill-opacity) hides what it is painted over

_SVG = "{http://www.w3.org/2000/svg}"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")


# ---------------------------------------------------------------- rendering

def render(svg: str, width: int, height: int, crisp: bool = False) -> np.ndarray:
    """Render `svg` at exactly width×height with resvg. (H, W, 4) uint8 RGBA.

    `crisp` turns anti-aliasing off (every pixel is painted by the one shape
    over its centre), which is what an id map needs."""
    kw = {"shape_rendering": "crisp_edges"} if crisp else {}
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height, skip_system_fonts=True, **kw)
    img = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    if img.size != (width, height):  # resvg honours aspect ratio; force exact size
        if crisp:
            img = img.resize((width, height), Image.Resampling.NEAREST)
        else:
            img = img.resize((width, height), Image.Resampling.LANCZOS)
    return np.asarray(img, dtype=np.uint8)


def rasterize(svg: str, width: int, height: int) -> np.ndarray:
    """Render `svg` at exactly width×height. Returns (H, W, 4) uint8 RGBA."""
    return render(svg, width, height)


def to_rgb_on_white(rgba: np.ndarray) -> np.ndarray:
    """Alpha-composite over white. Returns (H, W, 3) uint8."""
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgb * alpha + 255.0 * (1.0 - alpha)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 601 luma as float32 in 0..255."""
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    return 0.299 * r + 0.587 * g + 0.114 * b


# ---------------------------------------------------------------- fidelity

def _lab(rgb: np.ndarray) -> np.ndarray:
    return rgb2lab(rgb.astype(np.float64) / 255.0)


def delta_e_map(a_rgb: np.ndarray, b_rgb: np.ndarray) -> np.ndarray:
    """CIEDE2000 per pixel as a float64 (H, W) array."""
    return deltaE_ciede2000(_lab(a_rgb), _lab(b_rgb))


def delta_e(a_rgb: np.ndarray, b_rgb: np.ndarray) -> tuple[float, float]:
    """CIEDE2000 per pixel → (mean, 95th percentile)."""
    de = delta_e_map(a_rgb, b_rgb)
    return float(de.mean()), float(np.percentile(de, 95))


def _edges(rgb: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    return canny(luminance(rgb) / 255.0, sigma=sigma)


def _edge_f1(ea: np.ndarray, eb: np.ndarray, ea_wide: np.ndarray | None, tolerance_px: int) -> float:
    na, nb = int(ea.sum()), int(eb.sum())
    if na == 0 and nb == 0:
        return 1.0
    if na == 0 or nb == 0:
        return 0.0
    footprint = disk(tolerance_px)
    ea_wide = dilation(ea, footprint) if ea_wide is None else ea_wide
    precision = float((eb & ea_wide).sum()) / nb
    recall = float((ea & dilation(eb, footprint)).sum()) / na
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def edge_f1(a_rgb: np.ndarray, b_rgb: np.ndarray, tolerance_px: int = 2, sigma: float = 1.0) -> float:
    """Canny edges of both, matched within `tolerance_px`. F1 of precision/recall."""
    return _edge_f1(_edges(a_rgb, sigma), _edges(b_rgb, sigma), None, tolerance_px)


class Reference:
    """Everything about the source a score needs, computed once for every
    candidate traced from it: its colour on white in Lab, its edges, and the
    opaque interior the hole count looks in."""

    def __init__(self, src_rgba: np.ndarray):
        self.rgba = src_rgba
        self.height, self.width = src_rgba.shape[:2]
        self.rgb = to_rgb_on_white(src_rgba)
        self.lab = _lab(self.rgb)
        self.edges = _edges(self.rgb)
        self.edges_wide = dilation(self.edges, disk(2))
        a_src = src_rgba[..., 3].astype(np.float32) / 255.0
        self.opaque = ndimage.binary_erosion(a_src >= 0.99, structure=np.ones((3, 3), bool), border_value=0)

    def fidelity(self, out_rgba: np.ndarray) -> dict:
        out_rgb = to_rgb_on_white(out_rgba)
        de = deltaE_ciede2000(self.lab, _lab(out_rgb))
        return {"delta_e_mean": float(de.mean()), "delta_e_p95": float(np.percentile(de, 95)),
                "edge_f1": _edge_f1(self.edges, _edges(out_rgb), self.edges_wide, 2)}


# ---------------------------------------------------------------- transforms

def _matrix(transform: str | None) -> np.ndarray:
    m = np.eye(3)
    if not transform:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", transform):
        v = [float(x) for x in _NUM.findall(args)]
        t = np.eye(3)
        if name == "translate":
            t[0, 2], t[1, 2] = v[0], (v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            t[0, 0], t[1, 1] = v[0], (v[1] if len(v) > 1 else v[0])
        elif name == "rotate":
            a = math.radians(v[0])
            r = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
            if len(v) >= 3:
                c = np.array([[1, 0, v[1]], [0, 1, v[2]], [0, 0, 1]])
                ci = np.array([[1, 0, -v[1]], [0, 1, -v[2]], [0, 0, 1]])
                r = c @ r @ ci
            t = r
        elif name == "matrix" and len(v) == 6:
            t = np.array([[v[0], v[2], v[4]], [v[1], v[3], v[5]], [0, 0, 1]])
        m = m @ t
    return m


def _apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ m[:2, :2].T + m[:2, 2]


# ---------------------------------------------------------------- path → polylines

def _cubic(p0, c1, c2, p1) -> np.ndarray:
    n = int(min(400, max(4, math.ceil((np.linalg.norm(c1 - p0) + np.linalg.norm(c2 - c1) + np.linalg.norm(p1 - c2)) / STEP))))
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1


def _quad(p0, c, p1) -> np.ndarray:
    return _cubic(p0, p0 + 2.0 / 3.0 * (c - p0), p1 + 2.0 / 3.0 * (c - p1), p1)


def _arc(p0, rx, ry, phi_deg, large, sweep, p1) -> np.ndarray:
    """SVG endpoint arc → points (excluding p0), per the SVG implementation notes."""
    if rx == 0 or ry == 0 or np.allclose(p0, p1):
        return p1[None, :]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0 - p1) / 2.0
    x1 = cp * dx + sp * dy
    y1 = -sp * dx + cp * dy
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    if large == sweep:
        co = -co
    cx1 = co * rx * y1 / ry
    cy1 = -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2.0
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2.0

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = int(min(2000, max(4, math.ceil(abs(dt) * max(rx, ry) / STEP))))
    t = t1 + dt * np.linspace(0.0, 1.0, n + 1)[1:]
    x = cx + rx * np.cos(t) * cp - ry * np.sin(t) * sp
    y = cy + rx * np.cos(t) * sp + ry * np.sin(t) * cp
    pts = np.column_stack([x, y])
    pts[-1] = p1
    return pts


def path_polylines(d: str) -> list[tuple[np.ndarray, bool]]:
    """Every subpath of `d` as (points, closed). Handles the whole SVG path grammar."""
    out: list[tuple[np.ndarray, bool]] = []
    cur = np.zeros(2)
    start = np.zeros(2)
    pts: list[np.ndarray] = []
    last_c: np.ndarray | None = None  # reflected control for S/T
    last_cmd = ""

    def flush(closed: bool) -> None:
        nonlocal pts
        if pts:
            arr = np.vstack(pts)
            if len(arr) >= 2:
                out.append((arr, closed))
        pts = []

    for cmd, body in _CMD.findall(d):
        v = [float(x) for x in _NUM.findall(body)]
        rel = cmd.islower()
        c = cmd.upper()
        if c == "Z":
            if pts:
                if not np.allclose(pts[-1][-1], start):
                    pts.append(start[None, :].copy())
                flush(True)
            cur = start.copy()
            last_c = None
            last_cmd = c
            continue
        if c == "M":
            flush(False)
            p = cur + v[0:2] if rel else np.array(v[0:2])
            cur = start = p
            pts = [p[None, :].copy()]
            for k in range(2, len(v) - 1, 2):
                p = cur + v[k:k + 2] if rel else np.array(v[k:k + 2])
                pts.append(p[None, :])
                cur = p
            last_c = None
        elif c in "LHV":
            k = 0
            step = 2 if c == "L" else 1
            while k + step <= len(v):
                if c == "L":
                    p = cur + v[k:k + 2] if rel else np.array(v[k:k + 2])
                elif c == "H":
                    p = np.array([cur[0] + v[k] if rel else v[k], cur[1]])
                else:
                    p = np.array([cur[0], cur[1] + v[k] if rel else v[k]])
                pts.append(p[None, :])
                cur = p
                k += step
            last_c = None
        elif c in "CS":
            step = 6 if c == "C" else 4
            for k in range(0, len(v) - step + 1, step):
                base = cur if rel else np.zeros(2)
                if c == "C":
                    c1 = base + v[k:k + 2]
                    c2 = base + v[k + 2:k + 4]
                    p = base + v[k + 4:k + 6]
                else:
                    c1 = 2 * cur - last_c if (last_c is not None and last_cmd in "CS") else cur.copy()
                    c2 = base + v[k:k + 2]
                    p = base + v[k + 2:k + 4]
                pts.append(_cubic(cur, c1, c2, p))
                last_c = c2
                last_cmd = c
                cur = p
            continue
        elif c in "QT":
            step = 4 if c == "Q" else 2
            for k in range(0, len(v) - step + 1, step):
                base = cur if rel else np.zeros(2)
                if c == "Q":
                    q = base + v[k:k + 2]
                    p = base + v[k + 2:k + 4]
                else:
                    q = 2 * cur - last_c if (last_c is not None and last_cmd in "QT") else cur.copy()
                    p = base + v[k:k + 2]
                pts.append(_quad(cur, q, p))
                last_c = q
                last_cmd = c
                cur = p
            continue
        elif c == "A":
            for k in range(0, len(v) - 6, 7):
                p = cur + v[k + 5:k + 7] if rel else np.array(v[k + 5:k + 7])
                pts.append(_arc(cur, v[k], v[k + 1], v[k + 2], v[k + 3] != 0, v[k + 4] != 0, p))
                cur = p
            last_c = None
        last_cmd = c
    flush(False)
    return out


def _rect(x, y, w, h, rx, ry) -> np.ndarray:
    rx = min(rx, w / 2)
    ry = min(ry, h / 2)
    if rx <= 0 or ry <= 0:
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]], float)
    d = (f"M{x + rx} {y}L{x + w - rx} {y}A{rx} {ry} 0 0 1 {x + w} {y + ry}L{x + w} {y + h - ry}"
         f"A{rx} {ry} 0 0 1 {x + w - rx} {y + h}L{x + rx} {y + h}A{rx} {ry} 0 0 1 {x} {y + h - ry}"
         f"L{x} {y + ry}A{rx} {ry} 0 0 1 {x + rx} {y}Z")
    return path_polylines(d)[0][0]


def _ellipse(cx, cy, rx, ry) -> np.ndarray:
    n = max(16, int(math.ceil(2 * math.pi * max(rx, ry) / STEP)))
    t = np.linspace(0.0, 2 * math.pi, n + 1)
    return np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])


# ---------------------------------------------------------------- the drawing

@dataclass
class Contour:
    element: int            # index of the painted element, in paint order
    pts: np.ndarray         # (N, 2) source px
    closed: bool
    stroke: float | None    # stroke width in source px for a stroked centreline, else None
    paint: str = ""
    fill_rule: str = "nonzero"


@dataclass
class Drawing:
    contours: list[Contour] = field(default_factory=list)
    elements: int = 0
    segments: int = 0
    strokes: int = 0
    #: per element: whether it is opaque enough to hide what it is painted over
    covers: list[bool] = field(default_factory=list)


def _f(el: ET.Element, name: str, default: float = 0.0) -> float:
    v = el.get(name)
    if v is None:
        return default
    m = _NUM.search(v)
    return float(m.group(0)) if m else default


def _segments_in(d: str) -> int:
    n = 0
    for cmd, body in _CMD.findall(d):
        c = cmd.upper()
        k = len(_NUM.findall(body))
        n += {"M": max(0, k // 2 - 1), "L": k // 2, "H": k, "V": k, "C": k // 6, "S": k // 4, "Q": k // 4,
              "T": k // 2, "A": k // 7}.get(c, 0)
    return n


_PAINT = ("fill", "stroke", "stroke-width", "fill-rule", "fill-opacity", "stroke-opacity")


def _paint(el: ET.Element, inherited: dict) -> dict:
    out = dict(inherited)
    style = el.get("style") or ""
    for k in _PAINT:
        if el.get(k) is not None:
            out[k] = el.get(k)
        m = re.search(rf"(?:^|;)\s*{k}\s*:\s*([^;]+)", style)
        if m:
            out[k] = m.group(1).strip()
    return out


def _unit(v: str | None, default: float = 1.0) -> float:
    if v is None:
        return default
    m = _NUM.search(v)
    if not m:
        return default
    x = float(m.group(0))
    return x / 100.0 if v.strip().endswith("%") else x


def _opacity(el: ET.Element) -> float:
    style = el.get("style") or ""
    m = re.search(r"(?:^|;)\s*opacity\s*:\s*([^;]+)", style)
    return _unit(m.group(1) if m else el.get("opacity"))


def parse(svg: str, size: tuple[int, int] | None = None) -> Drawing:
    """The painted geometry of `svg` in source pixels: root user units mapped
    through the viewBox onto a `size` (w, h) raster when one is given."""
    root = ET.fromstring(svg)
    defs: dict[str, ET.Element] = {}
    for el in root.iter():
        if el.get("id") is not None:
            defs[el.get("id")] = el
    drawing = Drawing()
    base = np.eye(3)
    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or "")]
    if size is not None and len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        base = _matrix(f"scale({size[0] / vb[2]} {size[1] / vb[3]}) translate({-vb[0]} {-vb[1]})")

    def shape(el: ET.Element, m: np.ndarray, paint: dict, alpha: float) -> None:
        tag = el.tag.replace(_SVG, "")
        m = m @ _matrix(el.get("transform"))
        polys: list[tuple[np.ndarray, bool]] = []
        if tag == "path":
            d = el.get("d", "")
            polys = path_polylines(d)
            drawing.segments += _segments_in(d)
        elif tag == "rect":
            rx = el.get("rx")
            ry = el.get("ry")
            rxv = _f(el, "rx") if rx is not None else (_f(el, "ry") if ry is not None else 0.0)
            ryv = _f(el, "ry") if ry is not None else rxv
            polys = [(_rect(_f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height"), rxv, ryv), True)]
            drawing.segments += 8 if rxv > 0 else 4
        elif tag == "circle":
            r = _f(el, "r")
            polys = [(_ellipse(_f(el, "cx"), _f(el, "cy"), r, r), True)]
            drawing.segments += 4
        elif tag == "ellipse":
            polys = [(_ellipse(_f(el, "cx"), _f(el, "cy"), _f(el, "rx"), _f(el, "ry")), True)]
            drawing.segments += 4
        elif tag in ("polygon", "polyline"):
            v = [float(x) for x in _NUM.findall(el.get("points", ""))]
            p = np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)
            if tag == "polygon" and len(p):
                p = np.vstack([p, p[:1]])
            polys = [(p, tag == "polygon")]
            drawing.segments += max(0, len(p) - 1)
        elif tag == "line":
            polys = [(np.array([[_f(el, "x1"), _f(el, "y1")], [_f(el, "x2"), _f(el, "y2")]]), False)]
            drawing.segments += 1
        else:
            return
        paint = _paint(el, paint)
        alpha = alpha * _opacity(el)
        fill = paint.get("fill", "#000")
        stroke = paint.get("stroke")
        sw = paint.get("stroke-width")
        rule = "evenodd" if (paint.get("fill-rule") or "").strip() == "evenodd" else "nonzero"
        index = drawing.elements
        drawing.elements += 1
        lin = math.sqrt(abs(np.linalg.det(m[:2, :2])))
        filled = fill != "none" and tag != "line"
        stroked = bool(stroke) and stroke != "none"
        opaque = (filled and alpha * _unit(paint.get("fill-opacity")) >= COVER_ALPHA) or \
                 (stroked and alpha * _unit(paint.get("stroke-opacity")) >= COVER_ALPHA)
        drawing.covers.append(bool(opaque))
        if filled:
            for p, closed in polys:
                drawing.contours.append(Contour(index, _apply(m, p), True, None, fill, rule))
        if stroked:
            drawing.strokes += 1
            w = (float(_NUM.search(sw).group(0)) if sw and _NUM.search(sw) else 1.0) * lin
            for p, closed in polys:
                drawing.contours.append(Contour(index, _apply(m, p), closed, w, stroke))

    def walk(el: ET.Element, m: np.ndarray, paint: dict, alpha: float) -> None:
        tag = el.tag.replace(_SVG, "")
        if tag in ("defs", "linearGradient", "radialGradient", "filter", "mask", "clipPath", "symbol", "pattern",
                   "style", "title", "desc", "metadata"):
            return
        if tag == "use":
            ref = (el.get("href") or el.get(_XLINK) or "").lstrip("#")
            target = defs.get(ref)
            if target is None:
                return
            mm = m @ _matrix(el.get("transform")) @ _matrix(f"translate({_f(el, 'x')} {_f(el, 'y')})")
            shape(target, mm, _paint(el, paint), alpha * _opacity(el))
            return
        if tag in ("svg", "g"):
            mm = m if tag == "svg" else m @ _matrix(el.get("transform"))
            inner = _paint(el, paint)
            a = alpha if tag == "svg" else alpha * _opacity(el)
            for child in el:
                walk(child, mm, inner, a)
            return
        shape(el, m, paint, alpha)

    walk(root, base, {}, 1.0)
    return drawing


# ---------------------------------------------------------------- what is on screen

def _d(pts: np.ndarray, closed: bool) -> str:
    flat = np.round(pts, 3).ravel().tolist()
    return "M" + " ".join(map(repr, flat)) + ("Z" if closed else "")


def id_svg(drawing: Drawing, size: tuple[int, int]) -> str:
    """The drawing with every element that hides what lies under it painted in
    its own colour (element index + 1 as 0xRRGGBB), the others left out."""
    w, h = size
    by_el: dict[int, list[Contour]] = {}
    for c in drawing.contours:
        by_el.setdefault(c.element, []).append(c)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">']
    for e in sorted(by_el):
        if not drawing.covers[e]:
            continue
        colour = f"#{e + 1:06x}"
        fills = [c for c in by_el[e] if c.stroke is None]
        if fills:
            d = "".join(_d(c.pts, True) for c in fills)
            parts.append(f'<path d="{d}" fill="{colour}" fill-rule="{fills[0].fill_rule}"/>')
        for c in by_el[e]:
            if c.stroke is not None:
                parts.append(f'<path d="{_d(c.pts, c.closed)}" fill="none" stroke="{colour}" '
                             f'stroke-width="{c.stroke:.4f}" stroke-linejoin="round" stroke-linecap="round"/>')
    parts.append("</svg>")
    return "".join(parts)


def id_map(drawing: Drawing, size: tuple[int, int], scale: int = ID_SCALE) -> np.ndarray:
    """(h·scale, w·scale) int32: the index of the element on top at each
    sub-pixel, -1 where nothing opaque is. Rendered without anti-aliasing."""
    w, h = size
    if drawing.elements >= (1 << 24) - 1:
        raise ValueError("too many elements for an id map")
    rgba = render(id_svg(drawing, size), w * scale, h * scale, crisp=True)
    ids = (rgba[..., 0].astype(np.int32) << 16) | (rgba[..., 1].astype(np.int32) << 8) | rgba[..., 2].astype(np.int32)
    return np.where(rgba[..., 3] >= 128, ids - 1, -1).astype(np.int32)


def _lookup(ids: np.ndarray, pts: np.ndarray, scale: int) -> np.ndarray:
    hh, ww = ids.shape
    ix = np.floor(pts[:, 0] * scale).astype(np.int64)
    iy = np.floor(pts[:, 1] * scale).astype(np.int64)
    inside = (ix >= 0) & (ix < ww) & (iy >= 0) & (iy < hh)
    out = np.full(len(pts), -1, np.int32)
    out[inside] = ids[iy[inside], ix[inside]]
    return out


def visible_samples(q: np.ndarray, closed: bool, element: int, stroke: float | None,
                    ids: np.ndarray | None, scale: int) -> np.ndarray:
    """Which samples of an outline are on screen: not painted over, VIS_OFFSET
    px to either side of it (or on a stroke's centreline), by later elements."""
    if ids is None or len(q) == 0:
        return np.ones(len(q), bool)
    if stroke is not None:
        return ~(_lookup(ids, q, scale) > element)
    if closed:
        tan = np.roll(q, -1, axis=0) - np.roll(q, 1, axis=0)
    else:
        tan = np.gradient(q, axis=0) if len(q) > 1 else np.zeros_like(q)
    norm = np.linalg.norm(tan, axis=1, keepdims=True)
    tan = np.divide(tan, norm, out=np.zeros_like(tan), where=norm > 1e-12)
    normal = np.column_stack([-tan[:, 1], tan[:, 0]]) * VIS_OFFSET
    a = _lookup(ids, q + normal, scale) > element
    b = _lookup(ids, q - normal, scale) > element
    return ~(a & b)


# ---------------------------------------------------------------- per-contour measures

def _resample(pts: np.ndarray, closed: bool, step: float = STEP) -> np.ndarray:
    if closed and not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[:1]])
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    keep = np.concatenate([[True], seg > 1e-9])
    pts = pts[keep]
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    total = s[-1]
    if total < step:
        return pts
    n = max(2, int(round(total / step)))
    t = np.linspace(0.0, total, n + 1)
    if closed:
        t = t[:-1]
    return np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])


def _wrap(a: np.ndarray) -> np.ndarray:
    return (a + np.pi) % (2 * np.pi) - np.pi


def _turns(q: np.ndarray, closed: bool, k: int) -> np.ndarray:
    """Turning between successive chord directions of span k samples."""
    if closed:
        fwd = np.roll(q, -k, axis=0) - q
        ang = np.arctan2(fwd[:, 1], fwd[:, 0])
        return _wrap(np.roll(ang, -1) - ang)
    if len(q) <= k + 1:
        return np.zeros(0)
    fwd = q[k:] - q[:-k]
    ang = np.arctan2(fwd[:, 1], fwd[:, 0])
    return _wrap(np.diff(ang))


def _cancelled(q: np.ndarray, closed: bool, k: int) -> np.ndarray:
    """Turning cancelled inside a k-sample window, per sample.

    The fine turning |θ| at every sample, spread over the k samples a chord of
    span k can see it from, less the turning |Θ| of that chord as it slides
    past, placed at the chord's middle. Over a whole outline this sums to
    Σ|θ| − Σ|Θ|, the wobble; where the turning is monotone the two spread the
    same way, so a stretch of outline can be scored on its own (the part of it
    that is on screen) without its ends reading as wobble."""
    n = len(q)
    fine = np.abs(_turns(q, closed, 1))
    coarse = np.abs(_turns(q, closed, k))
    h = k // 2
    # A sharp turn at sample m bends every chord that has m inside it, so it
    # shows in the coarse turns centred at m − h .. m + h − 1 (k = 2h); the
    # fine turn is spread over the same k positions.
    if closed:
        c = np.roll(coarse, h)                   # c[p]: coarse turn of the chord centred at p
        ext = fine[np.arange(-h, n + k - h) % n]  # fine[i] is the turn at sample i + 1
        cs = np.concatenate([[0.0], np.cumsum(ext)])
        return (cs[k: k + n] - cs[:n]) / k - c
    # open: positions −h .. n + h − 1, so that no turning is lost off either end
    size = n + 2 * h
    f = np.zeros(size)
    f[h + 1: h + 1 + len(fine)] = fine            # fine turn i sits at sample i + 1
    c = np.zeros(size)
    c[2 * h: 2 * h + len(coarse)] = coarse        # coarse turn i between chords from i and i + 1
    cs = np.concatenate([[0.0], np.cumsum(np.concatenate([np.zeros(h - 1), f, np.zeros(k + 1)]))])
    return (cs[k: k + size] - cs[:size]) / k - c


def _flips(turn: np.ndarray, closed: bool, hyst: float) -> tuple[int, list[int]]:
    """Curvature sign changes: a zig-zag over the cumulative turning angle with
    `hyst` radians of threshold, so a lobe counts only if it turns that much.
    A closed curve is scanned twice and only the second lap counts, so where
    the scan starts does not matter. Returns (count, sample indices)."""
    n = len(turn)
    if n == 0:
        return 0, []
    seq = np.concatenate([turn, turn]) if closed else turn
    theta = np.cumsum(seq)
    direction = 0
    hi = lo = 0.0
    flips: list[int] = []
    for i, t in enumerate(theta):
        if direction >= 0:
            hi = max(hi, t)
        if direction <= 0:
            lo = min(lo, t)
        if direction >= 0 and hi - t >= hyst:
            if direction == 1 and (not closed or i >= n):
                flips.append(i % n)
            direction, lo = -1, t
        elif direction <= 0 and t - lo >= hyst:
            if direction == -1 and (not closed or i >= n):
                flips.append(i % n)
            direction, hi = 1, t
    return len(flips), flips


def _dilate(mask: np.ndarray, r: int, closed: bool) -> np.ndarray:
    if not mask.any() or r <= 0:
        return mask.copy()
    if closed:
        ext = np.concatenate([mask[-r:], mask, mask[:r]])
        return np.convolve(ext.astype(np.int32), np.ones(2 * r + 1, np.int32), "valid") > 0
    return np.convolve(mask.astype(np.int32), np.ones(2 * r + 1, np.int32), "same") > 0


def _inflections(q: np.ndarray, closed: bool, k: int) -> list[int]:
    """Curvature sign changes inside the smooth runs of an outline.

    A corner (CORNER_SNAP degrees turned within 2 px) and CORNER_GUARD px
    either side of it are cut out first: turning the other way at a corner is
    what a corner is, and a line meeting an arc there is not a wave. Inside a
    run the turning at a k-sample chord goes through `_flips`. What is left is
    a curve that turns one way and then the other with no corner between — the
    sag of a straight edge drawn as one S-shaped cubic, a sweep that wavers.
    Returns sample indices."""
    n = len(q)
    d = _turns(q, closed, 1)                      # d[i]: turn at sample i+1
    if len(d) < 2 * k:
        return []
    m = int(round(2.0 / STEP))
    if closed:
        ext = np.concatenate([d[-(m // 2):], d, d[: m - m // 2]])
    else:
        ext = np.concatenate([np.zeros(m // 2), d, np.zeros(m - m // 2)])
    cs = np.concatenate([[0.0], np.cumsum(ext)])
    local = cs[m:m + len(d)] - cs[:len(d)]        # turning within 2 px around each sample
    corner = _dilate(np.abs(local) >= math.radians(CORNER_SNAP), int(round(CORNER_GUARD / STEP)), closed)
    t = _turns(q, closed, k)                      # t[i]: turn between the chords from sample i and i+1
    if not corner.any():
        return _flips(t, closed, math.radians(INFLECT_HYST))[1] if closed else \
            [int(j + k // 2) for j in _flips(t, False, math.radians(INFLECT_HYST))[1]]
    # t[i] is inside a run when no sample from i to i+k+1 is at a corner
    cm = np.concatenate([corner, corner[: k + 2]]) if closed else np.concatenate([corner, np.ones(k + 2, bool)])
    ccs = np.concatenate([[0], np.cumsum(cm.astype(np.int32))])
    bad = (ccs[np.arange(len(t)) + k + 2] - ccs[np.arange(len(t))]) > 0
    good = ~bad
    if closed:  # rotate so the scan starts inside a corner: no run straddles the wrap
        s0 = int(np.flatnonzero(bad)[0])
        order = np.roll(np.arange(len(t)), -s0)
    else:
        order = np.arange(len(t))
    g = good[order]
    edges = np.flatnonzero(np.diff(np.concatenate([[0], g.astype(np.int8), [0]])))
    out: list[int] = []
    for a, b in zip(edges[::2], edges[1::2]):
        if (b - a) * STEP < 4.0:
            continue
        _, at = _flips(t[order[a:b]], False, math.radians(INFLECT_HYST))
        out.extend(int((order[a + j] + k // 2) % n) for j in at)
    return out


@dataclass
class CornerInfo:
    at: tuple[float, float]
    index: int              # sample index of the corner's middle
    lo: int                 # first and one-past-last sample of its curved span (may wrap)
    hi: int
    turn_deg: float
    radius: float


def _corners(q: np.ndarray, net_sign: float) -> list[CornerInfo]:
    """Convex corners of a closed outline turning CORNER_MIN_DEG..CORNER_MAX_DEG,
    each with the radius it is drawn at.

    A corner is seeded where CORNER_WINDOW px of outline turn at least
    CORNER_SEED_DEG, and then takes in the whole curve it sits on: the run of
    samples turning the same way faster than CURVED on either side of the
    window, up to GROW_MAX px. A rounded corner wider than the window is therefore one corner
    with the radius it is drawn at, not a shorter corner and two bent sides
    (which, depending on where the window happened to land, read as a bowed
    rect or as no rect at all)."""
    if len(q) < 16:
        return []
    d = _turns(q, True, 1)                       # d[i]: turn at sample i+1
    n = len(d)
    w = min(int(round(CORNER_WINDOW / STEP)), n - 1)
    h = w // 2
    idx = np.arange(n)
    ext = np.concatenate([d[-h:], d, d[: w - h + 1]])
    ce = np.concatenate([[0.0], np.cumsum(ext)])
    conv = (ce[idx + w] - ce[idx]) * net_sign     # turning over the window centred on each sample
    signed = d * net_sign
    curved_all = signed > CURVED * STEP
    grow = min(n - 1, int(round(GROW_MAX / STEP)))
    out: list[CornerInfo] = []
    taken = np.zeros(n, bool)
    tried = np.zeros(n, bool)     # samples of a curve already grown from a seed and turned down
    seed_rad = math.radians(CORNER_SEED_DEG)
    lo_rad = math.radians(CORNER_MIN_DEG)
    hi_rad = math.radians(CORNER_MAX_DEG)
    for i in np.argsort(-conv, kind="stable"):
        if conv[i] < seed_rad:
            break
        start = i - h
        window = np.arange(start, start + w) % n
        if tried[i] or taken[window].any():
            continue
        where = np.flatnonzero(curved_all[window])
        if len(where) == 0:
            continue
        a, b = start + int(where[0]), start + int(where[-1])   # first and last curved sample (unwrapped)
        # grow over the rest of the curve, no further than GROW_MAX
        while b - a < grow and curved_all[(a - 1) % n] and not taken[(a - 1) % n]:
            a -= 1
        while b - a < grow and curved_all[(b + 1) % n] and not taken[(b + 1) % n]:
            b += 1
        span = np.arange(a, b + 1) % n
        curved = curved_all[span]
        turn = float(signed[span][curved].sum())
        if not (lo_rad <= turn <= hi_rad):
            tried[span] = True
            continue
        taken[np.arange(min(a, start), max(b + 1, start + w)) % n] = True
        # The length over which it turns. A sharp vertex falls between two
        # resampled points and turns in one or two samples; a chamfer turns in
        # two separated spikes and reads as sharp, which is how it looks.
        length = float(curved.sum()) * STEP
        radius = max(0.0, length - 2 * STEP) / turn
        mid = int((a + b) // 2) % n
        out.append(CornerInfo((float(q[(mid + 1) % n, 0]), float(q[(mid + 1) % n, 1])), mid,
                              a % n, (b + 1) % n, math.degrees(turn), radius))
    out.sort(key=lambda c: c.index)
    return out


def _rect_like(q: np.ndarray, corners: list[CornerInfo], visible: np.ndarray | None = None) -> dict | None:
    """A contour with exactly four convex corners of 60°–120° and four sides
    that each turn less than RECT_SIDE_DEG is a (rounded) rectangle to a
    designer — or a parallelogram, or a square drawn as a pillow: its corners
    should share a radius, its sides should be straight and pairwise parallel.
    None when the contour is not one. With `visible`, only the corners and the
    stretches of side that are on screen are judged."""
    if len(corners) != 4:
        return None
    n = len(q)
    d = _turns(q, True, 1)
    bows, angles = [], []
    for a, b in zip(corners, corners[1:] + corners[:1]):
        lo, hi = (a.hi + 1) % n, b.lo
        m = (hi - lo) % n
        if m * STEP < 2.0:
            return None
        at = (lo + np.arange(m + 1)) % n
        side = q[at]
        if abs(math.degrees(float(d[(lo + np.arange(m)) % n].sum()))) > RECT_SIDE_DEG:
            return None
        c0 = side.mean(axis=0)
        _u, _sv, vt = np.linalg.svd(side - c0, full_matrices=False)
        direction = vt[0]
        normal = np.array([-direction[1], direction[0]])
        dev = np.abs((side - c0) @ normal)
        if visible is not None:
            dev = dev[visible[at]]
        bows.append(float(dev.max()) if len(dev) else 0.0)
        angles.append(math.degrees(math.atan2(direction[1], direction[0])))
    ref = angles[0]
    skew = max(abs(((ang - ref + 45.0) % 90.0) - 45.0) for ang in angles)
    if skew > RECT_GRID:
        return None  # a trapezoid, a rhombus: drawn that way on purpose
    shown = [c for c in corners if visible is None or visible[c.index % n]]
    r = [c.radius for c in shown]
    return {"radii": [c.radius for c in corners], "spread": (max(r) - min(r)) if len(r) >= 2 else 0.0,
            "mixed": len(r) >= 2 and max(r) >= ROUND_R and min(r) < SHARP_R, "bow": max(bows), "skew": skew}


def _area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# ---------------------------------------------------------------- holes

def holes(svg: str, src_rgba: np.ndarray, scale: int = HOLE_SCALE, opaque: np.ndarray | None = None) -> dict:
    h, w = src_rgba.shape[:2]
    if opaque is None:
        a_src = src_rgba[..., 3].astype(np.float32) / 255.0
        opaque = ndimage.binary_erosion(a_src >= 0.99, structure=np.ones((3, 3), bool), border_value=0)
    if not opaque.any():
        return {"hole_subpx": 0, "hole_px": 0.0, "hole_clusters": 0, "pinholes": 0, "_clusters": []}
    alpha = render(svg, w * scale, h * scale)[..., 3]
    thresh = HOLE_COVER * 255.0
    short = (alpha < thresh).reshape(h, scale, w, scale) & opaque[:, None, :, None]
    short = short.reshape(h * scale, w * scale)
    ys, xs = np.nonzero(short)
    if len(ys) == 0:
        return {"hole_subpx": 0, "hole_px": 0.0, "hole_clusters": 0, "pinholes": 0, "_clusters": []}
    cover = alpha[ys, xs].astype(np.float64) / 255.0
    lab, n = ndimage.label(short, structure=np.ones((3, 3), bool))
    ll = lab[ys, xs]
    sizes = np.bincount(ll, minlength=n + 1)[1:]
    sums = np.bincount(ll, weights=1.0 - cover, minlength=n + 1)[1:]
    cy = np.bincount(ll, weights=ys.astype(np.float64), minlength=n + 1)[1:] / np.maximum(sizes, 1)
    cx = np.bincount(ll, weights=xs.astype(np.float64), minlength=n + 1)[1:] / np.maximum(sizes, 1)
    mins = np.full(n + 1, np.inf)
    np.minimum.at(mins, ll, cover)
    mins = mins[1:]
    clusters = [{"x": float(cx[k]) / scale, "y": float(cy[k]) / scale, "subpx": int(sizes[k]),
                 "min_cover": float(mins[k]), "deficit_px": float(sums[k]) / scale ** 2,
                 "pinhole": bool(mins[k] < PINHOLE_COVER)} for k in range(n)]
    clusters.sort(key=lambda c: -c["deficit_px"])
    return {
        "hole_subpx": int(len(ys)),
        "hole_px": float((1.0 - cover).sum()) / scale ** 2,
        "hole_clusters": int(n),
        "pinholes": int((mins < PINHOLE_COVER).sum()),
        "_clusters": clusters,
    }


# ---------------------------------------------------------------- the scorecard

def geometry_card(svg: str, size: tuple[int, int] | None = None, visibility: bool = True,
                  id_scale: int = ID_SCALE) -> dict:
    """The outline measures of `svg`. With `visibility` (and a `size` to
    render at) only what is on screen is scored; see the module docstring."""
    drawing = parse(svg, size)
    ids = id_map(drawing, size, id_scale) if (visibility and size is not None and drawing.contours) else None
    length = 0.0
    hidden_len = 0.0
    wobble = 0.0
    inflections = 0
    slivers = 0
    sliver_area = 0.0
    degenerate = 0
    thin_strokes = 0
    radius_bad = 0
    rects = 0
    bowed = 0
    skewed = 0
    loc_wobble: list[tuple[float, float, float]] = []
    loc_flip: list[tuple[float, float]] = []
    loc_sliver: list[tuple[float, float, float, float]] = []
    loc_radius: list[tuple[float, float, list[float]]] = []
    kw = max(1, int(round(WOBBLE_SCALE / STEP)))
    ki = max(1, int(round(INFLECT_SCALE / STEP)))
    for c in drawing.contours:
        q = _resample(c.pts, c.closed)
        if len(q) < 3:
            if c.stroke is None:
                degenerate += 1
            elif c.stroke < THIN_STROKE and visible_samples(q, c.closed, c.element, c.stroke, ids, id_scale).any():
                thin_strokes += 1
            continue
        vis = visible_samples(q, c.closed, c.element, c.stroke, ids, id_scale)
        if c.stroke is not None and c.stroke < THIN_STROKE:
            if vis.any():
                thin_strokes += 1
                mid = c.pts[len(c.pts) // 2]
                loc_sliver.append((float(mid[0]), float(mid[1]), 0.0, c.stroke))
        seg = np.linalg.norm(np.diff(np.vstack([q, q[:1]]) if c.closed else q, axis=0), axis=1)
        per = float(seg.sum())
        if c.stroke is None:
            area = abs(_area(q))
            if area < 0.05:
                degenerate += 1
                continue
            if area < SLIVER_AREA or 2 * area / max(per, 1e-9) < SLIVER_THICK:
                if vis.any():
                    slivers += 1
                    sliver_area += area
                    cen = q.mean(axis=0)
                    loc_sliver.append((float(cen[0]), float(cen[1]), area, 2 * area / max(per, 1e-9)))
                continue  # a sliver's own turning is not an outline's wobble
        if per < 2 * WOBBLE_SCALE:
            continue
        shown = float(vis.mean()) * per
        hidden_len += per - shown
        if shown <= 0.0:
            continue
        length += shown
        excess_at = _cancelled(q, c.closed, kw)
        if c.closed:
            vis_at = vis
        else:
            h = kw // 2
            vis_at = np.concatenate([np.full(h, vis[0]), vis, np.full(h, vis[-1])])
        excess = max(0.0, float(excess_at[vis_at].sum()))
        wobble += math.degrees(excess)
        # where: cancelled turning summed over a 2L window, reported on screen only
        if excess > math.radians(5):
            box = np.ones(2 * kw)
            local = np.convolve(excess_at, box, "same")
            off = 0 if c.closed else kw // 2
            for j in np.nonzero((local > math.radians(20)) & vis_at)[0][:: kw]:
                p = q[min(max(j - off, 0), len(q) - 1)]
                loc_wobble.append((float(p[0]), float(p[1]), math.degrees(float(local[j]))))
        for j in _inflections(q, c.closed, ki):
            if vis[j % len(q)]:
                inflections += 1
                loc_flip.append((float(q[j, 0]), float(q[j, 1])))
        if c.closed and c.stroke is None:
            net = float(_turns(q, True, 1).sum())
            corners = _corners(q, 1.0 if net >= 0 else -1.0)
            rect = _rect_like(q, corners, vis)
            if rect is not None:
                rects += 1
                bad = rect["spread"] > RADIUS_SPREAD or rect["mixed"]
                radius_bad += int(bad)
                bowed += int(rect["bow"] > RECT_BOW)
                skewed += int(rect["skew"] > RECT_SKEW)
                if bad or rect["bow"] > RECT_BOW or rect["skew"] > RECT_SKEW:
                    cen = q.mean(axis=0)
                    loc_radius.append((float(cen[0]), float(cen[1]), [round(x, 2) for x in rect["radii"]],
                                       round(rect["bow"], 2), round(rect["skew"], 2)))
    per100 = 100.0 / length if length > 0 else 0.0
    return {
        "elements": drawing.elements,
        "segments": drawing.segments,
        "strokes": drawing.strokes,
        "outline_len_px": length,
        "hidden_len_px": hidden_len,
        "slivers": slivers,
        "sliver_area_px": sliver_area,
        "degenerate": degenerate,
        "thin_strokes": thin_strokes,
        "wobble_deg_100px": wobble * per100,
        "inflections": inflections,
        "rect_like": rects,
        "radius_inconsistent": radius_bad,
        "rect_bowed": bowed,
        "rect_skewed": skewed,
        "segments_100px": drawing.segments * per100,
        "_wobble_at": loc_wobble,
        "_flips_at": loc_flip,
        "_slivers_at": loc_sliver,
        "_radius_at": loc_radius,
    }


def scorecard(svg: str, src_rgba: np.ndarray, detail: bool = False, visibility: bool = True,
              hole_scale: int = HOLE_SCALE, id_scale: int = ID_SCALE, opaque: np.ndarray | None = None) -> dict:
    """Artifact counts for one trace. Keys starting with `_` (locations) only with detail=True."""
    card = {**holes(svg, src_rgba, hole_scale, opaque),
            **geometry_card(svg, (src_rgba.shape[1], src_rgba.shape[0]), visibility, id_scale)}
    card["artifact_index"] = artifact_index(card)
    if not detail:
        card = {k: v for k, v in card.items() if not k.startswith("_")}
    return card


def artifact_index(card: dict) -> float:
    """One number for ranking cleanliness, 0 = clean. Each term is scaled so
    that one clearly visible defect of its kind costs about 1."""
    return (
        1.0 * card["pinholes"]
        + 0.1 * max(0, card["hole_clusters"] - card["pinholes"])
        + 1.0 * card["slivers"]
        + 1.0 * card["degenerate"]
        + 1.0 * card["thin_strokes"]
        + 0.5 * (card["radius_inconsistent"] + card["rect_bowed"] + card["rect_skewed"])
        + 0.25 * card["wobble_deg_100px"]
        + 1.0 * card["inflections"]
    )


def is_clean(card: dict) -> bool:
    """No visible defect of any kind the bench tracks: no pinhole, no sliver or
    sub-pixel stroke, wobble under 25° per 100 px, no uneven rectangle. The same
    test `bench.presets_report` counts as CLEAN."""
    return (card["pinholes"] == 0
            and card["slivers"] + card["degenerate"] + card["thin_strokes"] == 0
            and card["wobble_deg_100px"] < 25.0
            and card["radius_inconsistent"] + card["rect_bowed"] + card.get("rect_skewed", 0) == 0)


def assess(svg: str, ref: Reference, hole_scale: int | None = None) -> dict:
    """Fidelity and the scorecard for one trace of `ref`'s source. The hole
    render drops to 2x above a megapixel of 4x render so a large image stays
    affordable; every candidate of one image is scored at the same scale."""
    if hole_scale is None:
        hole_scale = HOLE_SCALE if ref.width * ref.height <= 640 * 640 else 2
    out = render(svg, ref.width, ref.height)
    return {**ref.fidelity(out), **scorecard(svg, ref.rgba, hole_scale=hole_scale, opaque=ref.opaque)}


ARTIFACT_KEYS = (
    "hole_subpx", "hole_px", "hole_clusters", "pinholes", "slivers", "sliver_area_px", "degenerate", "thin_strokes",
    "wobble_deg_100px", "inflections", "rect_like", "radius_inconsistent", "rect_bowed", "rect_skewed", "elements", "segments",
    "segments_100px", "artifact_index",
)

LOWER_IS_BETTER = frozenset(ARTIFACT_KEYS) - {"rect_like", "elements", "segments"} | {
    "seam_ppm", "delta_e_mean", "delta_e_p95", "alpha_mae", "banding_index", "outline_px", "outline_p99_px",
    "junction_px", "line_debt_px", "line_debt_segments", "paths", "nodes", "bytes", "elapsed_ms"}
