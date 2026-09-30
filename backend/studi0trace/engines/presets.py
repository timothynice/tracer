"""Named parameter bundles, and Auto, which picks among them per image.

Each preset is a *trade-off*, not a secret better setting, and which one suits
an image is not something a user can tell by looking at it. So the first entry
is Auto (`kind="auto"`): it traces the image with every `auto_candidate`
preset and keeps the cleanest result that is as faithful as the best
(`studi0trace.auto`). Flat & poster and Cut file are never candidates: one is
a style, the other an output format, and only the user can want those.

The `detail` line of every preset is measured, never written by hand: it is
read from `preset_details.json` next to this file, which one command writes
from a run over the whole bench corpus with the engine as built:

    cd backend && VEXEL_BACKEND=rust RAYON_NUM_THREADS=1 \\
        .venv/bin/python -m bench.presets_eval --out /tmp/presets --fast --workers 3 --write-details

Re-run it whenever the engine, a preset or the corpus changes — a preset that
quotes a stale number is worse than one that quotes none.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

DETAILS_FILE = Path(__file__).with_name("preset_details.json")


class Preset(BaseModel):
    id: str
    label: str
    engine: str
    description: str = Field(description="What this preset is for, in the user's terms")
    detail: str = Field(description="What it measurably costs or buys, from the bench corpus")
    sample: str = Field(description="Thumbnail filename under /presets/")
    params: dict[str, Any]
    kind: Literal["auto", "preset"] = Field(
        "preset", description="`auto` traces with every candidate and picks per image; `preset` is a fixed bundle")
    auto_candidate: bool = Field(False, description="Whether Auto tries this preset")


def _measured() -> dict[str, str]:
    try:
        return dict(json.loads(DETAILS_FILE.read_text(encoding="utf-8")).get("lines", {}))
    except (OSError, ValueError):
        return {}


_DETAIL = _measured()
_UNMEASURED = "not measured yet"

# The bundles themselves are data, not code: `presets.json` is read here and embedded by the
# Rust core (`crates/studi0trace-core/src/presets.rs`), so the two cannot list different presets.
# "{candidates}" in Auto's description is filled from the candidates' own labels below, so it
# cannot drift from them.
BUNDLES_FILE = Path(__file__).with_name("presets.json")
_PRESETS: list[dict[str, Any]] = json.loads(BUNDLES_FILE.read_text(encoding="utf-8"))


def _and(words: list[str]) -> str:
    return words[0] if len(words) < 2 else f"{', '.join(words[:-1])} and {words[-1]}"


_CANDIDATE_LABELS = _and([p["label"] for p in _PRESETS if p.get("auto_candidate")])
PRESETS: list[Preset] = [
    Preset(engine="vexel", detail=_DETAIL.get(p["id"], _UNMEASURED),
           **{**p, "description": p["description"].replace("{candidates}", _CANDIDATE_LABELS)})
    for p in _PRESETS
]


def all_presets() -> list[Preset]:
    return list(PRESETS)


def fixed_presets() -> list[Preset]:
    """The presets that are one parameter bundle each (everything but Auto)."""
    return [p for p in PRESETS if p.kind == "preset"]


def auto_candidates() -> list[Preset]:
    """What Auto traces with, in preference order (a tie goes to the earlier)."""
    return [p for p in PRESETS if p.auto_candidate]
