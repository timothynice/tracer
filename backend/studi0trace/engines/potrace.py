"""Potrace: classic 1-bit outline tracing via the `potrace` CLI.

Potrace only understands bilevel bitmaps, so this engine flattens the RGBA
input onto white, converts to luminance and thresholds it. `threshold` and
`invert` are therefore the two parameters that most change the result.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import ClassVar, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from studi0trace.engines import registry
from studi0trace.engines.base import EngineError, TraceInput, TraceResult, finish

TurnPolicy = Literal["black", "white", "left", "right", "minority", "majority", "random"]


class PotraceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: int = Field(
        128, ge=0, le=255, description="Luminance cut-off: pixels darker than this become ink",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Bitmap"}},
    )
    invert: bool = Field(
        False, description="Trace the light regions instead of the dark ones",
        json_schema_extra={"ui": {"control": "toggle", "group": "Bitmap"}},
    )
    turdsize: int = Field(
        2, ge=0, le=100, description="Suppress speckles up to this many pixels",
        json_schema_extra={"ui": {"control": "slider", "step": 1, "group": "Cleanup", "label": "Speckle filter"}},
    )
    turnpolicy: TurnPolicy = Field(
        "minority", description="How to resolve ambiguous path turns",
        json_schema_extra={"ui": {"control": "select", "group": "Cleanup", "label": "Turn policy"}},
    )
    alphamax: float = Field(
        1.0, ge=0.0, le=1.3334, description="Corner threshold: 0 = all corners, 1.33 = no corners",
        json_schema_extra={"ui": {"control": "slider", "step": 0.05, "group": "Curves", "label": "Corner smoothing"}},
    )
    opticurve: bool = Field(
        True, description="Join adjacent Bezier segments where possible",
        json_schema_extra={"ui": {"control": "toggle", "group": "Curves", "label": "Optimize curves"}},
    )
    opttolerance: float = Field(
        0.2, ge=0.0, le=1.0, description="How aggressively curves may be merged",
        json_schema_extra={"ui": {"control": "slider", "step": 0.05, "group": "Curves", "label": "Curve tolerance"}},
    )


class PotraceEngine:
    id: ClassVar[str] = "potrace"
    label: ClassVar[str] = "Potrace"
    description: ClassVar[str] = "Classic black & white outline tracing. Crisp single-colour shapes."
    Params: ClassVar[type[BaseModel]] = PotraceParams

    def __init__(self, binary: str = "potrace"):
        self.binary = binary

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        p = params if isinstance(params, PotraceParams) else PotraceParams.model_validate(params)
        started = time.perf_counter()
        exe = shutil.which(self.binary)
        if exe is None:
            raise EngineError("potrace binary not found on PATH")

        bitmap = self._to_bilevel(image.image, p.threshold, p.invert)

        with tempfile.TemporaryDirectory(prefix="potrace-") as tmp:
            bmp_path = Path(tmp) / "in.bmp"
            svg_path = Path(tmp) / "out.svg"
            bitmap.save(bmp_path, "BMP")
            cmd = [
                exe, "--svg", "--output", str(svg_path),
                "--turdsize", str(p.turdsize),
                "--turnpolicy", p.turnpolicy,
                "--alphamax", f"{p.alphamax:.4f}",
            ]
            if p.opticurve:
                cmd += ["--opttolerance", f"{p.opttolerance:.4f}"]
            else:
                cmd.append("--longcurve")
            cmd.append(str(bmp_path))

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise EngineError(f"potrace exited {proc.returncode}: {proc.stderr.strip()[:300]}")
            svg_raw = svg_path.read_text(encoding="utf-8")

        return finish(svg_raw, image, started)

    @staticmethod
    def _to_bilevel(rgba: Image.Image, threshold: int, invert: bool) -> Image.Image:
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        lum = Image.alpha_composite(white, rgba).convert("L")
        if invert:
            return lum.point(lambda v: 0 if v > threshold else 255, mode="L").convert("1")
        return lum.point(lambda v: 255 if v > threshold else 0, mode="L").convert("1")


registry.register(PotraceEngine())
