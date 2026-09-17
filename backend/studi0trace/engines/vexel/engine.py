"""Vexel engine: orchestrates the pipeline and registers with the engine seam."""
from __future__ import annotations

import time
from typing import ClassVar, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import ndimage

from studi0trace.engines import registry
from studi0trace.engines.base import TraceInput, TraceResult, finish
from studi0trace.engines.vexel.boundary import contours, coverage_field
from studi0trace.engines.vexel.curves import CurveParams, fit_shape, shape_svg
from studi0trace.engines.vexel.fills import FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import MergeParams, merge_regions
from studi0trace.engines.vexel.order import enclosure, paint_order, shape_mask
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.posterize import posterize_regions
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.rescue import rescue_features

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'


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
    path_precision: int = Field(
        2, ge=0, le=4, description="Decimal places in coordinates",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Output"}},
    )


def _is_invisible(fill) -> bool:
    if isinstance(fill, Solid):
        return fill.rgba[3] < 2.0
    return all(s.rgba[3] < 2.0 for s in fill.stops) if fill.stops else True


class VexelEngine:
    id: ClassVar[str] = "vexel"
    label: ClassVar[str] = "Vexel"
    description: ClassVar[str] = "Studi0's fidelity-first engine: gradient-aware regions, sub-pixel edges, whole-shape fitting."
    Params: ClassVar[type[BaseModel]] = VexelParams

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
            interior = np.clip(ndimage.distance_transform_edt(m), 0.5, 2.0) / 2.0
            w = interior[m]
            fills[lab] = fit_fill(xs[m], ys[m], rgba255[m], fit_params, weights=w)
            visible[lab] = float(np.average(prep.alpha[m], weights=w)) > 0.01

    ids = [int(i) for i in np.unique(labels) if i != 0]
    fit_regions(ids)

    # Rescue thin strokes / small details that were swallowed by a neighbour:
    # pixels far from any boundary whose colour disagrees with their region's fill.
    residual = np.zeros((height, width), np.float32)
    for lab in ids:
        m = labels == lab
        pred = fills[lab].evaluate(xs[m], ys[m])
        residual[m] = np.sqrt(((rgba255[m] - pred) ** 2).sum(axis=1))
    labels, rescued = rescue_features(labels, residual, threshold=7.5 * p.detail, min_region=p.min_region)
    if rescued:
        ids = [int(i) for i in np.unique(labels) if i != 0]
        fills.clear()
        visible.clear()
        fit_regions(ids)

    def fill_at(lab: int, qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
        return fills[lab].evaluate(qx, qy)

    enc = enclosure(labels)
    order = paint_order(enc)
    stacked = p.layering == "stacked"
    curve_params = CurveParams(corner_threshold=p.corner_threshold, tol=p.curve_tolerance, shape_fitting=p.shape_fitting)

    invisible = {lab for lab in ids if not visible[lab] or _is_invisible(fills[lab])}
    defs: list[str] = []
    elements: list[str] = []
    for i, lab in enumerate(order):
        if lab in invisible:
            continue  # transparent canvas or hole: nothing to paint
        fill = fills[lab]
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
        elements.append(shape_svg(shape, attrs, p.path_precision))

    body = f"<defs>{''.join(defs)}</defs>" if defs else ""
    return f'<svg {SVG_NS} viewBox="0 0 {width} {height}">{body}{"".join(elements)}</svg>'


registry.register(VexelEngine())
