"""AutoTrace (https://github.com/autotrace/autotrace) through its CLI, colour mode.

AutoTrace reads a transparent pixel as its RGB, which resvg and most encoders
leave black, so a transparent background comes back as a black square. Like
the service's Potrace engine, this flattens the image onto white first.
`color_count` 0 is AutoTrace's own default: no colour reduction at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import ClassVar

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from studi0trace.engines.base import EngineError, TraceInput, TraceResult, finish

BINARY = os.environ.get("AUTOTRACE_BIN", "autotrace")


class AutoTraceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color_count: int = Field(0, ge=0, le=256, description="Colours to reduce to; 0 = no reduction (AutoTrace's default)",
                             json_schema_extra={"ui": {"control": "slider", "step": 1}})
    despeckle_level: int = Field(0, ge=0, le=20, description="Despeckle level, 0 = off (default)",
                                 json_schema_extra={"ui": {"control": "slider", "step": 1}})
    despeckle_tightness: float = Field(2.0, ge=0.0, le=8.0, description="Despeckle tightness (default 2.0)",
                                       json_schema_extra={"ui": {"control": "slider", "step": 0.5}})
    corner_threshold: float = Field(100.0, ge=0.0, le=180.0, description="Corner angle in degrees (default 100)",
                                    json_schema_extra={"ui": {"control": "slider", "step": 1}})
    error_threshold: float = Field(2.0, ge=0.0, le=10.0, description="Curve-fit error before subdividing, px (default 2.0)",
                                   json_schema_extra={"ui": {"control": "slider", "step": 0.1}})


class AutoTraceEngine:
    id: ClassVar[str] = "autotrace"
    label: ClassVar[str] = "AutoTrace"
    description: ClassVar[str] = "AutoTrace CLI, colour outline tracing (bench only)."
    Params: ClassVar[type[BaseModel]] = AutoTraceParams

    def missing(self) -> str | None:
        return None if shutil.which(BINARY) else f"`{BINARY}` not on PATH (brew install autotrace)"

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        p = params if isinstance(params, AutoTraceParams) else AutoTraceParams.model_validate(params)
        exe = shutil.which(BINARY)
        if exe is None:
            raise EngineError(self.missing())
        started = time.perf_counter()
        white = Image.new("RGBA", image.image.size, (255, 255, 255, 255))
        flat = Image.alpha_composite(white, image.image).convert("RGB")
        with tempfile.TemporaryDirectory(prefix="autotrace-") as tmp:
            src = Path(tmp) / "in.png"
            out = Path(tmp) / "out.svg"
            flat.save(src, "PNG")
            cmd = [exe, "-output-format", "svg", "-output-file", str(out),
                   "-color-count", str(p.color_count),
                   "-despeckle-level", str(p.despeckle_level),
                   "-despeckle-tightness", f"{p.despeckle_tightness:g}",
                   "-corner-threshold", f"{p.corner_threshold:g}",
                   "-error-threshold", f"{p.error_threshold:g}",
                   str(src)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not out.exists():
                raise EngineError(f"autotrace exited {proc.returncode}: {proc.stderr.strip()[:300]}")
            svg = out.read_text(encoding="utf-8", errors="replace")
        return finish(svg, image, started)


ENGINE = AutoTraceEngine()
