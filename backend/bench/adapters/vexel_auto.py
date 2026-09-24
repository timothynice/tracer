"""Vexel's Auto preset as one bench engine.

What the API's `auto=true` does (`studi0trace.api.routes._run_auto`), without
the concurrency: trace the image with every Auto candidate preset
(`studi0trace.engines.presets.auto_candidates`), score each against the source
with `studi0trace.auto.assess`, and keep the one `studi0trace.auto.choose`
picks. The candidates run one after another here, so `elapsed_ms` is the whole
cost of Auto on one core's worth of scheduling: every candidate's trace plus
the scoring, not the fastest candidate. Which preset won is in `notes`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from studi0trace import auto
from studi0trace.engines import registry
from studi0trace.engines.base import EngineError, TraceInput, TraceResult


@dataclass(frozen=True)
class AutoTraceResult(TraceResult):
    notes: dict = field(default_factory=dict)


class VexelAutoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VexelAutoEngine:
    id: ClassVar[str] = "vexel-auto"
    label: ClassVar[str] = "Vexel (Auto)"
    description: ClassVar[str] = "Vexel's Auto preset: every candidate traced, the cleanest of the most faithful kept (bench only)."
    Params: ClassVar[type[BaseModel]] = VexelAutoParams

    def missing(self) -> str | None:
        registry.load_builtin()
        try:
            registry.get("vexel")
        except registry.UnknownEngine:
            return "the vexel engine is not registered"
        return None

    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult:
        started = time.perf_counter()
        engine = registry.get("vexel")
        ref = auto.reference(image)
        rows = []
        for preset in auto.candidates("vexel"):
            try:
                res = engine.trace(image, engine.Params.model_validate(preset.params))
            except Exception:  # noqa: BLE001 - as in the API, a failed candidate is left out
                continue
            rows.append((preset, res, auto.assess(res.svg, ref)))
        if not rows:
            raise EngineError("every Auto candidate failed")
        pick, reason = auto.choose([auto.Scored(p.id, s["delta_e_mean"], s["edge_f1"], s["artifact_index"],
                                                int(s["elements"])) for p, _r, s in rows])
        chosen = next(r for p, r, _s in rows if p.id == pick.id)
        return AutoTraceResult(svg=chosen.svg, elapsed_ms=(time.perf_counter() - started) * 1000.0,
                               stats=chosen.stats, notes={"pick": pick.id, "reason": reason,
                                                          "pick_trace_ms": chosen.elapsed_ms})


ENGINE = VexelAutoEngine()
