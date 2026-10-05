"""VTracer: colour tracing via the Rust `vtracer` library.

Input is written as an RGBA PNG so transparency survives; the previous
implementation flattened to RGB and lost it.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import ClassVar, Literal

import vtracer
from pydantic import BaseModel, ConfigDict, Field

from studi0trace.engines import registry
from studi0trace.engines.base import EngineError, TraceInput, TraceResult, finish


class VTracerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colormode: Literal["color", "binary"] = Field(
        "color", description="Full colour or black & white",
        json_schema_extra={"ui": {"control": "select", "group": "Colour", "label": "Colour mode"}},
    )
    hierarchical: Literal["stacked", "cutout"] = Field(
        "stacked", description="Stacked layers or non-overlapping cut-outs",
        json_schema_extra={"ui": {"control": "select", "group": "Colour", "label": "Layering"}},
    )
    mode: Literal["spline", "polygon", "none"] = Field(
        "spline", description="Curve fitting mode",
        json_schema_extra={"ui": {"control": "select", "group": "Curves", "label": "Curve mode"}},
    )
    filter_speckle: int = Field(
        4, ge=0, le=128, description="Discard patches smaller than this many pixels",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Cleanup", "label": "Speckle filter"}},
    )
    color_precision: int = Field(
        6, ge=1, le=8, description="Significant bits per colour channel",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Colour", "label": "Colour precision"}},
    )
    layer_difference: int = Field(
        16, ge=0, le=255, description="Colour distance between consecutive layers",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Colour", "label": "Layer difference"}},
    )
    corner_threshold: int = Field(
        60, ge=0, le=180, description="Angle (degrees) above which a point becomes a corner",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Curves", "unit": "°", "label": "Corner threshold"}},
    )
    length_threshold: float = Field(
        4.0, ge=3.5, le=10.0, description="Minimum segment length before subdividing",
        json_schema_extra={"ui": {"control": "slider", "step": 0.5, "group": "Curves", "label": "Segment length"}},
    )
    max_iterations: int = Field(
        10, ge=1, le=100, description="Curve fitting iterations",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Curves", "label": "Max iterations"}},
    )
    splice_threshold: int = Field(
        45, ge=0, le=180, description="Angle (degrees) above which two splines are spliced",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Curves", "unit": "°", "label": "Splice threshold"}},
    )
    path_precision: int = Field(
        3, ge=1, le=10, description="Decimal places in path coordinates",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Output", "label": "Path precision"}},
    )


class VTracerEngine:
    id: ClassVar[str] = "vtracer"
    label: ClassVar[str] = "VTracer"
    description: ClassVar[str] = "Colour-preserving tracing with stacked layers. Good for flat illustrations."
    Params: ClassVar[type[BaseModel]] = VTracerParams

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        p = params if isinstance(params, VTracerParams) else VTracerParams.model_validate(params)
        started = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="vtracer-") as tmp:
            png_path = Path(tmp) / "in.png"
            svg_path = Path(tmp) / "out.svg"
            image.image.save(png_path, "PNG")
            try:
                vtracer.convert_image_to_svg_py(str(png_path), str(svg_path), **p.model_dump())
            except Exception as exc:  # the Rust binding raises plain Exception/BaseException subclasses
                raise EngineError(f"vtracer failed: {exc}") from exc
            if not svg_path.exists():
                raise EngineError("vtracer produced no output")
            svg_raw = svg_path.read_text(encoding="utf-8")

        return finish(svg_raw, image, started)


registry.register(VTracerEngine())
