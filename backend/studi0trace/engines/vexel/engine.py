"""Vexel engine: orchestrates the pipeline and registers with the engine seam."""
from __future__ import annotations

import time
from typing import ClassVar, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import ndimage
from skimage.segmentation import relabel_sequential

from studi0trace.engines import registry
from studi0trace.engines.base import TraceInput, TraceResult, finish
from studi0trace.engines.vexel.boundary import contours, coverage_field, thin_coverage
from studi0trace.engines.vexel.curves import CurveParams, fit_shape, shape_svg
from studi0trace.engines.vexel.fills import FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import MergeParams, adjacency, merge_regions
from studi0trace.engines.vexel.order import enclosure, paint_order, shape_mask
from studi0trace.engines.vexel.overlaps import decompose_overlaps
from studi0trace.engines.vexel.shadows import ShadowPlan, detect_shadows, shadow_filter_svg
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.posterize import posterize_regions
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.refine import refine_merge
from studi0trace.engines.vexel.rescue import rescue_features
from studi0trace.engines.vexel.strokes import is_thin, stroke_fidelity, stroke_geometry, stroke_svg
from studi0trace.engines.vexel.weights import interior_weights

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'
_CROSS = ndimage.generate_binary_structure(2, 1)


class VexelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        svg = trace_rgba(rgba, p)
        return finish(svg, image, started)


def trace_rgba(rgba: np.ndarray, p: VexelParams) -> str:
    height, width = rgba.shape[:2]
    prep = prepare(rgba)
    grad = discontinuity(prep.features)
    labels0 = initial_labels(grad, prep.features, min_region=p.min_region)
    labels = merge_regions(labels0, prep.features, MergeParams(detail=p.detail, gradients=p.gradients), grad)
    if not p.gradients:
        labels = posterize_regions(labels, prep.features, p.detail, p.min_region, grad)

    ys, xs = np.mgrid[0:height, 0:width]
    xs = xs.astype(np.float64) + 0.5
    ys = ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)

    fit_params = FitParams(gradients=p.gradients, max_stops=p.max_stops, tol=max(2.0, p.detail / 2.0))
    fills: dict[int, object] = {}
    visible: dict[int, bool] = {}

    def fit_regions(target: list[int]) -> None:
        for lab in target:
            m = labels == lab
            # Boundary pixels are anti-aliasing mixtures: weight by distance into the
            # region so the fill (and the visibility test) is driven by pure pixels.
            w = interior_weights(m)
            fills[lab] = fit_fill(xs[m], ys[m], rgba255[m], fit_params, weights=w)
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
    residual = np.zeros((height, width), np.float32)
    for lab in ids:
        m = labels == lab
        pred = fills[lab].evaluate(xs[m], ys[m])
        r = np.sqrt(((rgba255[m] - pred) ** 2).sum(axis=1))
        base = 7.5 * p.detail
        inliers = r[r < base]  # the swallowed feature itself must not inflate the scale
        fit_rms = float(np.sqrt(np.mean(inliers * inliers))) if inliers.size else 0.0
        residual[m] = r / max(base, 4.0 * fit_rms)
    labels, rescued = rescue_features(labels, residual, threshold=1.0, min_region=p.min_region)
    if rescued:
        ids = [int(i) for i in np.unique(labels) if i != 0]
        fills.clear()
        visible.clear()
        fit_regions(ids)

    # Join gradient fragments (glows, off-centre radials) that one real fill explains.
    labels, fills, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, fit_params, edge_limit=0.6 * p.detail)
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
                w = interior_weights(m)
                fills[lab] = fit_fill(xs[m], ys[m], shadow_plan.corrected[m], fit_params, weights=w)

    # Thin regions are drawn lines. A single line often arrives as several
    # regions (split at junctions, broken by anti-aliasing gaps), so thin regions
    # that touch and share an ink colour are grouped and stroked together.
    stroke_of: dict[int, tuple[str, str]] = {}  # first member label -> (colour, svg)
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
                for n in neighbours.get(t, ()):
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
                        # each rim pixel joins the nearest opaque neighbour
                        dists = np.stack([ndimage.distance_transform_edt(labels != n)[m] for n in cands])
                        nearest = np.asarray(cands)[np.argmin(dists, axis=0)]
                        new = labels.copy()
                        new[m] = nearest
                        labels = new
                    fills.pop(t, None)
                    visible.pop(t, None)
                    order.remove(t)
                    invisible.discard(t)
                thin_labels = [lab for lab in thin_labels if lab not in absorbed]
                enc = enclosure(labels)
        groups = _group_thin(thin_labels, labels, prep.rgb, prep.alpha, fill_at)
        transparent = np.isin(labels, list(invisible)) if invisible else np.zeros_like(labels, dtype=bool)
        for members in groups:
            union = np.isin(labels, members)
            # grow one pixel into transparent surroundings so the faint outer
            # anti-aliasing of a sub-pixel line counts towards its ink area
            grown = union | (ndimage.binary_dilation(union, _CROSS) & transparent)
            field = thin_coverage(grown, labels, prep.rgb, prep.alpha, fill_at)
            stroke = stroke_geometry(union, field)
            if stroke is None:
                continue
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

    # Overlaps: a region whose colour is a blend of two neighbours, and whose
    # union with the top neighbour is a simpler shape, is two overlapping shapes
    # with the top one semi-transparent. The overlap region itself is dropped.
    mask_override: dict[int, np.ndarray] = {}
    fill_override: dict[int, Solid] = {}
    if p.overlaps and stacked:
        dec = decompose_overlaps(labels, fills, visible, curve_params, tol=fit_params.tol)
        if not dec.empty and not (dec.removed & skip):
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

    def fill_at_visible(q_lab: int, qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
        """Colour actually visible at (qx, qy) when q_lab's footprint was extended:
        the fill of whichever original region lies under each pixel."""
        under = labels[np.clip(qy.astype(int), 0, height - 1), np.clip(qx.astype(int), 0, width - 1)]
        out = np.empty((qx.size, 4))
        for lab_u in np.unique(under):
            sel = under == lab_u
            out[sel] = fills[int(lab_u)].evaluate(qx[sel], qy[sel]) if int(lab_u) in fills else fill_at(q_lab, qx[sel], qy[sel])
        return out

    defs: list[str] = []
    elements: list[str] = []
    for i, lab in enumerate(order):
        if lab in invisible:
            continue  # transparent canvas or hole: nothing to paint
        if lab in stroke_of:
            elements.append(stroke_of[lab][1])
        if lab in skip:
            continue
        fill = fill_override.get(lab, fills[lab])
        shadow = shadow_plan.shadows.get(lab)
        extra = ""
        if shadow is not None:
            defs.append(shadow_filter_svg(shadow, f"s{i + 1}", p.path_precision))
            extra = f' filter="url(#s{i + 1})"'
        if lab in mask_override:
            mask = mask_override[lab]
            field = coverage_field(mask, lab, labels, prep.rgb, prep.alpha, fill_at_visible)
            polys = contours(field)
            if not polys:
                continue
            polys.sort(key=lambda c: -abs(0.5 * (np.dot(c[:, 0], np.roll(c[:, 1], -1)) - np.dot(c[:, 1], np.roll(c[:, 0], -1)))))
            shape = fit_shape(polys, curve_params)
            d, attrs = fill.svg(f"g{i + 1}", p.path_precision)
            if d:
                defs.append(d)
            elements.append(shape_svg(shape, attrs + extra, p.path_precision))
            continue
        mask = shape_mask(labels, lab, enc, stacked, invisible)
        field = coverage_field(mask, lab, labels, prep.rgb, prep.alpha, fill_at)
        polys = contours(field)
        if not polys:
            continue
        polys.sort(key=lambda c: -abs(0.5 * (np.dot(c[:, 0], np.roll(c[:, 1], -1)) - np.dot(c[:, 1], np.roll(c[:, 0], -1)))))
        shape = fit_shape(polys, curve_params)
        d, attrs = fill.svg(f"g{i + 1}", p.path_precision)
        if d:
            defs.append(d)
        elements.append(shape_svg(shape, attrs + extra, p.path_precision))

    body = f"<defs>{''.join(defs)}</defs>" if defs else ""
    return f'<svg {SVG_NS} viewBox="0 0 {width} {height}">{body}{"".join(elements)}</svg>'


registry.register(VexelEngine())
