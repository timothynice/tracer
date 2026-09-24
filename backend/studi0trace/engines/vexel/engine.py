"""Vexel engine: orchestrates the pipeline and registers with the engine seam.

Two implementations of the pipeline live behind this seam. `vexel_rs` is the
Rust one and is used when it imports; the Python below is the reference it was
ported from, and stays the definition of what Vexel does. `VEXEL_BACKEND`
selects explicitly (`rust` | `python`), which is how the two are compared.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import ClassVar, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import ndimage
from skimage.segmentation import relabel_sequential

from studi0trace.engines import registry
from studi0trace.engines.base import TraceInput, TraceResult, finish
from studi0trace.engines.vexel.boundary import thin_coverage
from studi0trace.engines.vexel import dump, refine_render, reuse
from studi0trace.engines.vexel.curves import CurveParams, PathShape, Shape, fit_shape, shape_svg
from studi0trace.engines.vexel.fills import FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import MergeParams, adjacency, merge_regions
from studi0trace.engines.vexel.order import enclosure, paint_order, shape_labels, shape_mask
from studi0trace.engines.vexel import topology
from studi0trace.engines.vexel.overlaps import decompose_overlaps
from studi0trace.engines.vexel.shadows import ShadowPlan, detect_shadows, shadow_filter_svg
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.posterize import posterize_regions
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.refine import refine_merge
from studi0trace.engines.vexel.rescue import edge_mix, rescue_features
from studi0trace.engines.vexel.upsample import halve, upsample2x, wants_upsample
from studi0trace.engines.vexel.strokes import is_thin, stroke_fidelity, stroke_geometry, stroke_svg
from studi0trace.engines.vexel.weights import interior, interior_weights

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'
_CROSS = ndimage.generate_binary_structure(2, 1)

try:  # pragma: no cover - exercised by whichever backend is installed
    import vexel_rs as _vexel_rs
except ImportError:  # the crate is not built in this checkout
    _vexel_rs = None


def backend() -> str:
    """Which implementation `trace()` will use: "rust" or "python"."""
    want = os.environ.get("VEXEL_BACKEND", "").strip().lower()
    if want == "python":
        return "python"
    if want == "rust":
        if _vexel_rs is None:
            raise RuntimeError("VEXEL_BACKEND=rust but the vexel_rs extension is not installed")
        return "rust"
    return "rust" if _vexel_rs is not None else "python"


class VexelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upsample: Literal["auto", "never", "always"] = Field(
        "auto",
        description="Trace a small input (≤ 192 px) at twice its size when its own trace shows a region thinner than 2.2 px",
        json_schema_extra={"ui": {"control": "select", "group": "Regions", "label": "Small-input upsampling"}},
    )
    detail: float = Field(
        6.0, ge=1.0, le=40.0, description="Colour difference (ΔE) below which neighbouring regions merge; lower keeps more regions",
        json_schema_extra={"ui": {"control": "slider", "step": 0.5, "group": "Regions"}},
    )
    min_region: int = Field(
        6, ge=1, le=200, description="Regions smaller than this many pixels are absorbed",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Regions", "label": "Speckle floor", "unit": "px"}},
    )
    gradients: bool = Field(
        True, description="Reconstruct linear and radial gradients instead of flat bands",
        json_schema_extra={"ui": {"control": "toggle", "group": "Fills"}},
    )
    max_stops: int = Field(
        4, ge=2, le=8, description="Maximum colour stops per gradient",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Fills", "label": "Gradient stops"}},
    )
    layering: Literal["stacked", "cutout"] = Field(
        "stacked", description="Stacked shapes in painter's order (seamless, editable) or exact non-overlapping cut-outs",
        json_schema_extra={"ui": {"control": "select", "group": "Output"}},
    )
    corner_threshold: float = Field(
        60.0, ge=20.0, le=150.0, description="Turning angle (degrees) above which an outline point is a corner",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Curves", "unit": "°"}},
    )
    curve_tolerance: float = Field(
        0.4, ge=0.1, le=2.0, description="Maximum distance (px) between the fitted curve and the traced outline",
        json_schema_extra={"ui": {"control": "slider", "step": 0.05, "group": "Curves", "unit": "px"}},
    )
    shape_fitting: bool = Field(
        True, description="Emit circles, ellipses and rectangles as primitives when they fit",
        json_schema_extra={"ui": {"control": "toggle", "group": "Curves", "label": "Whole-shape fitting"}},
    )
    refine: bool = Field(
        False, description="Render each edge's two shapes and nudge the curve until the pixels match the source (slow)",
        json_schema_extra={"ui": {"control": "toggle", "group": "Curves", "label": "Render refinement"}},
    )
    strokes: bool = Field(
        True, description="Recover thin lines as stroked centreline paths instead of filled slivers",
        json_schema_extra={"ui": {"control": "toggle", "group": "Curves", "label": "Stroke recovery"}},
    )
    shadows: bool = Field(
        True, description="Rebuild drop shadows, glows and inner shadows as SVG filters instead of banded paths",
        json_schema_extra={"ui": {"control": "toggle", "group": "Effects"}},
    )
    stroke_tolerance: float = Field(
        0.2, ge=0.05, le=1.0,
        description="Largest error a centreline may leave before the thin region is drawn filled instead of stroked; lower keeps more shapes filled",
        json_schema_extra={"ui": {"control": "slider", "step": 0.01, "group": "Curves", "label": "Stroke tolerance"}},
    )
    overlaps: bool = Field(
        True, description="Rebuild semi-transparent overlaps as two overlapping shapes with opacity",
        json_schema_extra={"ui": {"control": "toggle", "group": "Fills", "label": "Overlap decomposition"}},
    )
    path_precision: int = Field(
        2, ge=0, le=4, description="Decimal places in coordinates",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Output"}},
    )


def _touching_pairs(labels: np.ndarray, of_interest: set[int]) -> set[tuple[int, int]]:
    """8-connected neighbour pairs (a < b) among `of_interest`, in one pass.

    Equivalent to asking whether a 3x3 dilation of one region meets the other,
    but without materialising a mask per region: on busy art that is thousands
    of full-frame arrays and gigabytes of peak memory.
    """
    h, w = labels.shape
    out: set[tuple[int, int]] = set()
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = labels[max(0, -dy) : h - max(0, dy), max(0, -dx) : w - max(0, dx)]
        b = labels[max(0, dy) : h - max(0, -dy), max(0, dx) : w - max(0, -dx)]
        diff = a != b
        if not diff.any():
            continue
        pa, pb = a[diff], b[diff]
        keep = np.isin(pa, list(of_interest)) & np.isin(pb, list(of_interest))
        if not keep.any():
            continue
        lo = np.minimum(pa[keep], pb[keep])
        hi = np.maximum(pa[keep], pb[keep])
        out.update(map(tuple, np.unique(np.stack([lo, hi], axis=1), axis=0).tolist()))
    return out


def _group_thin(thin_labels: list[int], labels: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, fill_at, colour_tol: float = 30.0) -> list[list[int]]:
    """Union-find over thin regions that touch (within one pixel) and have
    similar ink colour. Returns groups of labels."""
    if not thin_labels:
        return []

    inks: dict[int, np.ndarray] = {}
    for lab in thin_labels:
        m = labels == lab
        field = thin_coverage(m, labels, rgb, alpha, fill_at)
        w = np.maximum(field[m], 1e-3) ** 2
        inks[lab] = np.average(rgb[m], axis=0, weights=w)
    parent = {lab: lab for lab in thin_labels}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    touching = _touching_pairs(labels, set(thin_labels))
    # Same order the pairwise scan used, so the union-find roots — and every
    # ordering decision downstream of them — are unchanged.
    for i, a in enumerate(thin_labels):
        for b in thin_labels[i + 1 :]:
            if np.linalg.norm(inks[a] - inks[b]) > colour_tol:
                continue
            if (min(a, b), max(a, b)) in touching:
                parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for lab in thin_labels:
        groups.setdefault(find(lab), []).append(lab)
    return list(groups.values())


def split_rim(labels: np.ndarray, rim: np.ndarray, cands: list[int], xs: np.ndarray, ys: np.ndarray,
              rgba255: np.ndarray, fill_at) -> np.ndarray:
    """Hand every pixel of `rim` to the nearest of `cands`.

    A rim is a pixel or two wide, so exact distance ties are the rule, not the
    exception: a pixel one step from each of two regions has to go somewhere,
    and the ring downstream is drawn around whichever it joins. The rim pixel's
    own colour breaks the tie — it is an anti-aliased mixture, and the fill it is
    closer to is the region it is mostly made of — and the lower label breaks
    what is left, so the answer never rests on the order the candidates came in.
    """
    cands = sorted(cands)
    dists = np.stack([ndimage.distance_transform_edt(labels != n)[rim] for n in cands])
    qx, qy, colour = xs[rim], ys[rim], rgba255[rim][:, :3]
    off = np.stack([np.linalg.norm(colour - fill_at(n, qx, qy)[:, :3], axis=1) for n in cands])
    nearest = dists.min(axis=0)
    off = np.where(dists <= nearest + 1e-9, off, np.inf)
    out = labels.copy()
    out[rim] = np.asarray(cands)[np.argmin(off, axis=0)]
    return out


def _ring_area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


@dataclass
class _Record:
    """One painted shape as the assembly sees it: the labels it paints, a
    whole-shape primitive or the rings whose fitted arcs make its path, its paint."""

    member: frozenset[int]
    primitive: Shape | None
    rings: list
    attrs: str


def _shape_from_rings(bnd, rings, member, params: CurveParams):
    """A shape's geometry, assembled from the arcs its rings walk.

    A whole-shape primitive is still tried, but only for a shape that is one
    closed ring: a circle or a rectangle is a claim about the whole outline, and
    a shape whose outline is stitched from arcs it shares with several
    neighbours is not one. The arcs themselves are already fitted, so this
    reuses them and nothing is described twice.
    """
    if len(rings) == 1 and params.shape_fitting:
        primitive = bnd.primitive(rings[0]) or fit_shape([bnd.polyline(rings[0])], params)
        if not isinstance(primitive, PathShape):
            return primitive
    return PathShape(contours=[bnd.segments(r, member) for r in rings])


def _emit(pending: list, precision: int, defs: list[str]) -> list[str]:
    """Shapes to markup in paint order, repeated shapes as `<use>` of one
    definition each; stroke markup passes through where it stands."""
    shapes = [(item, k) for k, item in enumerate(pending) if not isinstance(item, str)]
    use_defs, use_elements = reuse.emit([(shape, attrs) for (shape, attrs), _k in shapes], precision)
    defs.extend(use_defs)
    out = [item if isinstance(item, str) else "" for item in pending]
    for ((_shape, _attrs), k), markup in zip(shapes, use_elements):
        out[k] = markup
    return out


def _is_opaque(fill) -> bool:
    if isinstance(fill, Solid):
        return fill.rgba[3] >= 250.0
    return bool(fill.stops) and all(s.rgba[3] >= 250.0 for s in fill.stops)


def _holes_to_fill(labels: np.ndarray, member: frozenset[int], rank: dict[int, int], opaque: set[int]) -> frozenset[int]:
    """The labels in every hole of `member` that holds only opaque regions
    painted after the shape (whose own rank is the lowest of its members)."""
    own = min(rank.get(m, -1) for m in member)
    outside = ~np.isin(labels, list(member))
    comp, n = ndimage.label(outside, structure=np.ones((3, 3), bool))
    border = np.unique(np.concatenate([comp[0], comp[-1], comp[:, 0], comp[:, -1]]))
    out: set[int] = set()
    for k in range(1, n + 1):
        if k in border:
            continue
        labs = np.unique(labels[comp == k])
        if all(int(v) in opaque and rank.get(int(v), -1) > own for v in labs):
            out.update(int(v) for v in labs)
    return frozenset(out)


def _is_invisible(fill) -> bool:
    if isinstance(fill, Solid):
        return fill.rgba[3] < 2.0
    return all(s.rgba[3] < 2.0 for s in fill.stops) if fill.stops else True


class VexelEngine:
    id: ClassVar[str] = "vexel"
    label: ClassVar[str] = "Vexel"
    description: ClassVar[str] = "Studi0's fidelity-first engine: gradient-aware regions, sub-pixel edges, whole-shape fitting."
    Params: ClassVar[type[BaseModel]] = VexelParams
    primary: ClassVar[bool] = True

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        p = params if isinstance(params, VexelParams) else VexelParams.model_validate(params)
        started = time.perf_counter()
        rgba = np.asarray(image.image.convert("RGBA"), dtype=np.uint8)
        if backend() == "rust":
            # The bytes are copied because the trace runs with the GIL released,
            # so it cannot hold a reference into a Python buffer. One copy of
            # 4·w·h against a couple of hundred milliseconds of tracing.
            height, width = rgba.shape[:2]
            svg = _vexel_rs.trace(rgba.tobytes(), width, height, p.model_dump())
        else:
            svg = trace_rgba(rgba, p)
        return finish(svg, image, started)


def trace_rgba(rgba: np.ndarray, p: VexelParams) -> str:
    height, width = rgba.shape[:2]
    prep = prepare(rgba)
    grad = discontinuity(prep.features)
    labels0 = initial_labels(grad, prep.features, min_region=p.min_region)
    labels = merge_regions(labels0, prep.features, MergeParams(detail=p.detail, gradients=p.gradients), grad)
    dump.labels("labels_merge", labels)
    if not p.gradients:
        labels = posterize_regions(labels, prep.features, p.detail, p.min_region, grad)

    ys, xs = np.mgrid[0:height, 0:width]
    xs = xs.astype(np.float64) + 0.5
    ys = ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)

    fit_params = FitParams(gradients=p.gradients, max_stops=p.max_stops, tol=max(2.0, p.detail / 2.0))
    fills: dict[int, object] = {}
    visible: dict[int, bool] = {}

    core_map = np.zeros((height, width), bool)  # every region's fill core (`weights.fill_core`)

    def fit_regions(target: list[int]) -> None:
        for lab in target:
            m = labels == lab
            # Boundary pixels are anti-aliasing mixtures: weight by distance into the
            # region so the fill (and the visibility test) is driven by pure pixels.
            w, core = interior(m)
            core_map[m] = core
            fills[lab] = fit_fill(xs[m], ys[m], rgba255[m], fit_params, weights=w, core=core)
            visible[lab] = float(np.average(prep.alpha[m], weights=w)) > 0.04

    ids = [int(i) for i in np.unique(labels) if i != 0]
    fit_regions(ids)

    # Transparency is one region. The inpainting under alpha = 0 leaves colour
    # seams that fragment the background into many invisible pieces; fold them
    # into a single label so ordering, adjacency and the rescue residual see one
    # transparent field (and its faint ink stands out against it).
    clear = [lab for lab in ids if not visible[lab]]
    if len(clear) > 1:
        keep = clear[0]
        labels = np.where(np.isin(labels, clear[1:]), keep, labels)
        labels, _, _ = relabel_sequential(labels)
        labels = labels.astype(np.int32)
        ids = [int(i) for i in np.unique(labels) if i != 0]
        fills.clear()
        visible.clear()
        fit_regions(ids)

    # Rescue thin strokes / small details that were swallowed by a neighbour:
    # pixels far from any boundary whose colour disagrees with their region's fill.
    # The residual is normalised per region by its own fit error, so a smooth
    # region that a gradient model fits imperfectly is not shredded into
    # fragments; only pixels that are outliers *for their region* qualify.
    # A pixel's colour counts in proportion to how much of it shows: under a
    # transparent pixel the colour is inpainted and means nothing, and measuring
    # it left a whole transparent field hovering at the threshold, where the
    # last bits of the fill decided which of its inpainting seams were "features".
    residual = np.zeros((height, width), np.float32)
    pred = np.zeros((height, width, 4))
    for lab in ids:
        m = labels == lab
        pred[m] = fills[lab].evaluate(xs[m], ys[m])
        diff = rgba255[m] - pred[m]
        cover = prep.alpha[m]
        r = np.sqrt(cover * cover * (diff[:, :3] ** 2).sum(axis=1) + diff[:, 3] ** 2)
        base = 7.5 * p.detail
        inliers = r[r < base]  # the swallowed feature itself must not inflate the scale
        fit_rms = float(np.sqrt(np.mean(inliers * inliers))) if inliers.size else 0.0
        residual[m] = r / max(base, 4.0 * fit_rms)
    # Near an edge, a pixel the edge's anti-aliasing or ringing explains is not
    # part of a feature, and does not count towards promoting one.
    explained = edge_mix(labels, rgba255, pred, prep.alpha, residual > 1.0)
    dump.labels("labels_clear", labels)
    labels, rescued = rescue_features(labels, residual, threshold=1.0, min_region=p.min_region, explained=explained, core=core_map)
    dump.labels("labels_rescue", labels)
    if rescued:
        ids = [int(i) for i in np.unique(labels) if i != 0]
        fills.clear()
        visible.clear()
        fit_regions(ids)

    # Join gradient fragments (glows, off-centre radials) that one real fill explains.
    labels, fills, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, fit_params, edge_limit=0.6 * p.detail)
    dump.labels("labels_refine", labels)
    if changed:
        ids = [int(i) for i in np.unique(labels) if i != 0]
        visible.clear()
        for lab in ids:
            m = labels == lab
            visible[lab] = float(np.average(prep.alpha[m], weights=interior_weights(m))) > 0.04

    def fill_at(lab: int, qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
        return fills[lab].evaluate(qx, qy)

    enc = enclosure(labels)
    order = paint_order(enc)
    stacked = p.layering == "stacked"
    curve_params = CurveParams(corner_threshold=p.corner_threshold, tol=p.curve_tolerance, shape_fitting=p.shape_fitting)

    invisible = {lab for lab in ids if not visible[lab] or _is_invisible(fills[lab])}

    # Soft shadows are blurred copies of a shape, not colour fields. Where the
    # blur model explains a band group better than the bands do, the bands are
    # dropped and the caster carries an SVG filter instead.
    shadow_plan = ShadowPlan()
    if p.shadows:
        shadow_plan = detect_shadows(
            labels, fills, visible,
            lambda lab: shape_mask(labels, lab, enc, stacked, invisible),
            prep, xs, ys, min_region=p.min_region,
        )
        if shadow_plan.corrected is not None:
            # The backdrop's fill was partly modelling the shadow's faint outer
            # reach; refit it against colours with the shadow taken back out.
            for lab in shadow_plan.refit:
                m = labels == lab
                w, core = interior(m)
                fills[lab] = fit_fill(xs[m], ys[m], shadow_plan.corrected[m], fit_params, weights=w, core=core)

    # Thin regions are drawn lines. A single line often arrives as several
    # regions (split at junctions, broken by anti-aliasing gaps), so thin regions
    # that touch and share an ink colour are grouped and stroked together.
    stroke_of: dict[int, tuple[str, str]] = {}  # first member label -> (colour, svg)
    stroked: dict[int, int] = {}  # label painted by a stroke along its middle -> the stroke's first member
    skip: set[int] = set(shadow_plan.absorbed)
    if p.strokes:
        thin_labels = [lab for lab in order if lab not in invisible and is_thin(labels == lab)]
        # A thin region that matches the colour of an adjacent large region is that
        # region's anti-aliased rim, not a line: fold it in so its neighbour's
        # coverage contour handles it, instead of stroking a hairline around it.
        if thin_labels:
            nbr_edges = adjacency(labels)
            neighbours: dict[int, set[int]] = {}
            for a, b in nbr_edges:
                neighbours.setdefault(a, set()).add(b)
                neighbours.setdefault(b, set()).add(a)
            thin_set = set(thin_labels)
            absorbed: dict[int, list[int]] = {}  # rim label -> opaque neighbours to split it among
            for t in thin_labels:
                m = labels == t
                field = thin_coverage(m, labels, prep.rgb, prep.alpha, fill_at)
                w = np.maximum(field[m], 1e-3) ** 2
                ink = np.average(prep.rgb[m], axis=0, weights=w)
                mean_alpha = float(prep.alpha[m].mean())
                cx, cy = float(np.average(xs[m], weights=w)), float(np.average(ys[m], weights=w))
                opaque_nbrs = []
                colour_match = False
                # Sorted: the order candidates are tried in must not depend on
                # how a set happens to hash its members.
                for n in sorted(neighbours.get(t, ())):
                    if n in thin_set or n in invisible:
                        continue
                    n_fill = fills[n].evaluate(np.array([cx]), np.array([cy]))[0]
                    if n_fill[3] > 128:
                        opaque_nbrs.append(n)
                    if np.linalg.norm(ink - n_fill[:3]) < 5.0 * p.detail:
                        colour_match = True
                # a rim: same colour as a neighbour, or nearly transparent and hugging opaque shapes
                if opaque_nbrs and (colour_match or mean_alpha < 0.2):
                    absorbed[t] = opaque_nbrs
            if absorbed:
                for t, cands in absorbed.items():
                    m = labels == t
                    if len(cands) == 1:
                        labels = np.where(m, cands[0], labels)
                    else:
                        labels = split_rim(labels, m, cands, xs, ys, rgba255, fill_at)
                    fills.pop(t, None)
                    visible.pop(t, None)
                    order.remove(t)
                    invisible.discard(t)
                thin_labels = [lab for lab in thin_labels if lab not in absorbed]
                enc = enclosure(labels)
        groups = _group_thin(thin_labels, labels, prep.rgb, prep.alpha, fill_at)
        dump.text("strokes", f"thin={thin_labels} groups={groups}\n")
        transparent = np.isin(labels, list(invisible)) if invisible else np.zeros_like(labels, dtype=bool)
        for members in groups:
            union = np.isin(labels, members)
            # grow one pixel into transparent surroundings so the faint outer
            # anti-aliasing of a sub-pixel line counts towards its ink area
            grown = union | (ndimage.binary_dilation(union, _CROSS) & transparent)
            field = thin_coverage(grown, labels, prep.rgb, prep.alpha, fill_at)
            stroke = stroke_geometry(union, field)
            if stroke is None:
                dump.text("strokes", f"group {members}: no geometry\n")
                continue
            dump.text("strokes", "group %s: width=%.6f polylines=%s closed=%s fidelity=%.6f\n" % (
                members, stroke.width, [len(q) for q in stroke.polylines], stroke.closed, stroke_fidelity(stroke, field)))
            # A letterform is thin, elongated and of consistent width — it passes
            # every geometric test for being a stroke, and stroking it mangles
            # its terminals and joins. Only the reconstruction tells them apart:
            # what would a constant-width centreline actually paint here?
            if stroke_fidelity(stroke, field) > p.stroke_tolerance:
                continue
            # ink colour from the purest (highest-coverage) pixels; opacity 1 because
            # the width already accounts for partial coverage
            cov = field[union]
            wts = np.maximum(cov, 1e-3) ** 2
            rgb = np.average(prep.rgb[union], axis=0, weights=wts)
            colour = "#%02x%02x%02x" % tuple(int(round(float(v))) for v in np.clip(rgb, 0, 255))
            el = stroke_svg(stroke, colour, 1.0, curve_params, p.path_precision)
            if el:
                first = min(members, key=order.index)
                stroke_of[first] = (colour, el)
                skip.update(members)
                stroked.update({m: first for m in members})

    # Overlaps: a region whose colour is a blend of two neighbours, and whose
    # union with the top neighbour is a simpler shape, is two overlapping shapes
    # with the top one semi-transparent. The overlap region itself is dropped.
    mask_override: dict[int, np.ndarray] = {}
    fill_override: dict[int, Solid] = {}
    over_backdrop: set[int] = set()
    if p.overlaps and stacked:
        dec = decompose_overlaps(labels, fills, visible, curve_params, tol=fit_params.tol)
        if not dec.empty and not (dec.removed & skip):
            over_backdrop = set(dec.over_backdrop)
            skip |= dec.removed
            mask_override.update(dec.masks)
            fill_override.update(dec.fills)
            # a top shape paints after everything it lies on
            for _ in range(len(dec.above)):
                moved = False
                for top, below in dec.above:
                    if top in order and below in order and order.index(top) < order.index(below):
                        order.remove(top)
                        order.insert(order.index(below) + 1, top)
                        moved = True
                if not moved:
                    break

    # A stroked region is painted by a line of one width along its middle, which
    # cannot follow the region's own outline; its neighbours stop at that
    # outline, and nothing is bled into it (the line would not hide the bleed),
    # so wherever the line falls short of it the canvas showed through. The
    # earliest neighbour painted before the line fills the region underneath,
    # as a designer draws a line over the ground it sits on.
    underlay: dict[int, set[int]] = {}
    if stacked and stroked:
        nbrs: dict[int, set[int]] = {}
        for a, b in adjacency(labels):
            nbrs.setdefault(a, set()).add(b)
            nbrs.setdefault(b, set()).add(a)
        for t in sorted(stroked):
            if t not in order:
                continue
            cands = [n for n in nbrs.get(t, ()) if n in order and n not in skip and n not in invisible
                     and order.index(n) < order.index(stroked[t])]
            if cands:
                underlay.setdefault(min(cands, key=order.index), set()).add(t)

    # The boundary, once: every edge between two regions is placed sub-pixel and
    # fitted a single time, so the two regions that share it are handed the same
    # curve and cannot leave a hairline between them.
    dump.labels("labels_to_topology", labels)

    # A small input with thin features is traced again at twice its size; the
    # viewBox carries the scale. See `upsample.py` for the evidence.
    if p.upsample == "always" or (p.upsample == "auto" and wants_upsample(labels, height, width)):
        dump.text("upsample", "2x\n")
        return halve(trace_rgba(upsample2x(rgba), p.model_copy(update={"upsample": "never"})), width, height)
    bnd = topology.build(
        labels, prep.rgb, prep.alpha, fill_at, curve_params,
        rank={lab: i for i, lab in enumerate(order)} if stacked else None,
        # Nothing bleeds under paint that does not hide it: a top an overlap
        # made translucent, or a region drawn as a line along its middle.
        see_through={lab for lab in fill_override if fill_override[lab].rgba[3] < 250} | set(stroked),
        painted_by={t: owner for owner, ts in underlay.items() for t in ts},
    )
    dump.arcs("arcs", bnd)

    rank_of = {lab: i for i, lab in enumerate(order)}
    # Regions a shape may be laid under without being seen through them: an
    # opaque fill; a top an overlap made translucent over the backdrop (its
    # colour was solved over that backdrop, which is what then lies beneath);
    # and a region painted some other way (a stroke along its middle, a blend
    # an overlap explains, a shadow band), which wants paint under it.
    opaque = {lab for lab in order if lab not in invisible and _is_opaque(fill_override.get(lab, fills[lab]))}
    opaque |= over_backdrop | (skip - invisible)

    # Every shape as it stands in the graph, fitted once: a record holds the
    # labels it paints, a whole-shape primitive or the rings whose fitted arcs
    # make its path, and its paint. A stroke's finished markup stands as a string.
    defs: list[str] = []
    records: list[_Record | str] = []
    for i, lab in enumerate(order):
        if lab in invisible:
            continue  # transparent canvas or hole: nothing to paint
        if lab in stroke_of:
            records.append(stroke_of[lab][1])
        if lab in skip:
            continue
        fill = fill_override.get(lab, fills[lab])
        shadow = shadow_plan.shadows.get(lab)
        extra = ""
        if shadow is not None:
            defs.append(shadow_filter_svg(shadow, f"s{i + 1}", p.path_precision))
            extra = f' filter="url(#s{i + 1})"'
        member = shape_labels(lab, enc, stacked, invisible) | frozenset(underlay.get(lab, ()))
        if lab in mask_override:
            # An overlap's shape is the union of its own region and the blends
            # it explains: those are labels in the one graph, so its outline is
            # the graph's too and tiles with every neighbour, instead of a
            # separate trace of the union that nothing else shared.
            member = member | frozenset(int(v) for v in np.unique(labels[mask_override[lab]]))
        rings = [r for r in bnd.rings(member) if r]
        if stacked and len(rings) > 1:
            # A hole whose every region is painted later, opaquely, is not cut:
            # this shape paints on underneath them, as a designer lays a shape
            # down and puts the others on top. Cut, the hole's outline was a
            # second copy of theirs, bled to meet it, and every flaw in that
            # copy (a hairpin, a bleed that turned a sliver's ring inside out
            # under even-odd) was a pinhole where nothing painted at all.
            filled = _holes_to_fill(labels, member, rank_of, opaque)
            if filled:
                member = member | filled
                rings = [r for r in bnd.rings(member) if r]
        if not rings:
            continue
        rings.sort(key=lambda r: -_ring_area(bnd.polyline(r)))
        primitive = None
        if len(rings) == 1 and curve_params.shape_fitting:
            # a ring `topology._rectify` made a rectangle is one already; the
            # arcs carry the same outline, so a neighbour's edge agrees with it
            candidate = bnd.primitive(rings[0]) or fit_shape([bnd.polyline(rings[0])], curve_params)
            if not isinstance(candidate, PathShape):
                primitive = candidate
        d, attrs = fill.svg(f"g{i + 1}", p.path_precision)
        if d:
            defs.append(d)
        records.append(_Record(frozenset(member), primitive, rings, attrs + extra))

    def shape_of(rec: _Record) -> Shape:
        if rec.primitive is not None:
            return rec.primitive
        return PathShape(contours=[bnd.segments(r, rec.member) for r in rec.rings])

    if p.refine:
        # Ask the renderer: the two shapes on either side of each arc, drawn as
        # they stand, against the source pixels along the arc.
        all_defs = "".join(defs)

        def neighbours(labels: tuple[int, ...]) -> tuple[str, list[str]]:
            # a blurred shape is left out of the crop: the Rust twin's
            # rasteriser has no filters, and the two must decide alike
            return all_defs, [
                refine_render.element_markup(shape_of(rec), rec.attrs, p.path_precision)
                for rec in records if not isinstance(rec, str) and (rec.member & set(labels)) and "filter=" not in rec.attrs
            ]

        refine_render.refine(bnd.arcs, neighbours, rgba)

    pending: list[tuple[Shape, str] | str] = [rec if isinstance(rec, str) else (shape_of(rec), rec.attrs) for rec in records]
    elements = _emit(pending, p.path_precision, defs)
    body = f"<defs>{''.join(defs)}</defs>" if defs else ""
    return f'<svg {SVG_NS} viewBox="0 0 {width} {height}">{body}{"".join(elements)}</svg>'


registry.register(VexelEngine())
