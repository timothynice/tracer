"""ImageTracer.js (https://github.com/jankovicsandras/imagetracerjs) under Node.

The image goes to `imagetracer.js` (beside this file) as an RGBA PNG, so
ImageTracer sees transparency and quantises in RGBA as it does in a browser.
The module is found through $IMAGETRACER_DIR, the npm project it was
installed into (`npm install imagetracerjs pngjs`), or NODE_PATH.

`elapsed_ms` is ImageTracer's own time as the script measures it (PNG decode,
trace, SVG serialisation); Node's start-up, ~50 ms a call, is left out so the
harness does not count against the tracer.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from studi0trace.engines.base import EngineError, TraceInput, TraceResult, finish

SCRIPT = Path(__file__).with_name("imagetracer.js")
Preset = Literal["default", "posterized1", "posterized2", "posterized3", "curvy", "sharp", "detailed", "smoothed",
                 "grayscale", "fixedpalette", "randomsampling1", "randomsampling2", "artistic1", "artistic2",
                 "artistic3", "artistic4"]


def _node_env() -> dict[str, str]:
    env = dict(os.environ)
    paths = [p for p in env.get("NODE_PATH", "").split(os.pathsep) if p]
    if env.get("IMAGETRACER_DIR"):
        paths.insert(0, str(Path(env["IMAGETRACER_DIR"]) / "node_modules"))
    env["NODE_PATH"] = os.pathsep.join(paths)
    return env


class ImageTracerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: Preset = Field("default", description="One of ImageTracer's built-in option presets",
                           json_schema_extra={"ui": {"control": "select"}})
    seed: int = Field(1, ge=1, description="Seed for Math.random (the random-sampling presets)",
                      json_schema_extra={"ui": {"control": "slider", "step": 1}})


class ImageTracerEngine:
    id: ClassVar[str] = "imagetracer"
    label: ClassVar[str] = "ImageTracer.js"
    description: ClassVar[str] = "ImageTracer.js 1.2.x under Node (bench only)."
    Params: ClassVar[type[BaseModel]] = ImageTracerParams

    def missing(self) -> str | None:
        if shutil.which("node") is None:
            return "`node` not on PATH"
        probe = subprocess.run(["node", "-e", "require.resolve('imagetracerjs'); require.resolve('pngjs')"],
                               capture_output=True, text=True, env=_node_env(), timeout=60, cwd=SCRIPT.parent)
        if probe.returncode != 0:
            return "imagetracerjs/pngjs not resolvable (npm install imagetracerjs pngjs; set IMAGETRACER_DIR)"
        return None

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        p = params if isinstance(params, ImageTracerParams) else ImageTracerParams.model_validate(params)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="imagetracer-") as tmp:
            src = Path(tmp) / "in.png"
            image.image.save(src, "PNG")
            proc = subprocess.run(["node", str(SCRIPT), str(src), p.preset, str(p.seed)],
                                  capture_output=True, text=True, env=_node_env(), timeout=900)
        if proc.returncode != 0:
            raise EngineError(f"imagetracer exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        out = json.loads(proc.stdout)
        result = finish(out["svg"], image, started)
        return replace(result, elapsed_ms=float(out["ms"]))


ENGINE = ImageTracerEngine()
