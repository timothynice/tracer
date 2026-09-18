"""The engine seam: what every tracer receives and what it must return.

`Engine.trace()` is synchronous and CPU-bound. Engines never touch asyncio;
the API layer runs them in worker threads.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel

from studi0trace.imaging.svg import SvgStats, normalize_dimensions, svg_stats


@dataclass(frozen=True)
class TraceInput:
    image: Image.Image  # always RGBA
    width: int
    height: int
    source_bytes: bytes
    source_format: str  # "PNG" | "JPEG" | "GIF" | "WEBP" | "BMP"


@dataclass(frozen=True)
class TraceResult:
    svg: str  # viewBox-normalised, no width/height on the root element
    elapsed_ms: float
    stats: SvgStats


class EngineError(Exception):
    """An engine failed in a way that has a user-safe message."""


@runtime_checkable
class Engine(Protocol):
    id: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    #: Shown in the app's own UI. The others stay on the API and in the bench,
    #: where comparing engines is the point.
    primary: ClassVar[bool]

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult: ...


def finish(svg_raw: str, image: TraceInput, started: float) -> TraceResult:
    """Normalise dimensions, gather stats and stamp timing. Call at the end of trace()."""
    svg = normalize_dimensions(svg_raw, image.width, image.height)
    return TraceResult(svg=svg, elapsed_ms=(time.perf_counter() - started) * 1000.0, stats=svg_stats(svg))
