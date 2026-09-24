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

_PRESETS: list[dict[str, Any]] = [
    dict(
        id="auto",
        label="Auto",
        kind="auto",
        description="Traces your image with Balanced, Logo & icon, Detailed and Simplified, and keeps the "
                    "cleanest result that is as faithful as the best. Start here.",
        sample="auto.png",
        params={},
    ),
    dict(
        id="balanced",
        label="Balanced",
        auto_candidate=True,
        description="Gradients, shadows, strokes and overlaps all reconstructed. The most faithful all-rounder.",
        sample="balanced.png",
        params={},
    ),
    dict(
        id="logo",
        label="Logo & icon",
        auto_candidate=True,
        description="Merges harder and fits whole shapes, for a small, clean, hand-editable file. Best where "
                    "the mark matters more than the last half-pixel.",
        sample="logo.png",
        params={"detail": 10.0, "min_region": 16, "curve_tolerance": 0.6, "corner_threshold": 70.0},
    ),
    dict(
        id="detailed",
        label="Detailed illustration",
        auto_candidate=True,
        description="Keeps subtler colour steps and more gradient stops, and fits a little tighter. For "
                    "illustration where soft shading matters.",
        sample="detailed.png",
        params={"detail": 3.5, "min_region": 6, "max_stops": 6, "curve_tolerance": 0.4},
    ),
    dict(
        id="dense",
        label="Simplified",
        auto_candidate=True,
        description="Fewest shapes; the cleanest on busy art and soft shadows. Skips stroke, overlap and "
                    "shadow recovery and merges hard.",
        sample="dense.png",
        params={"detail": 14.0, "min_region": 24, "strokes": False, "overlaps": False, "shadows": False},
    ),
    dict(
        id="flat",
        label="Flat & poster",
        description="A style choice, not a quality setting: solid colours only, no gradients and no filters. "
                    "Also the safest file for an old importer. Auto never picks it.",
        sample="flat.png",
        params={"gradients": False, "shadows": False, "detail": 14.0, "min_region": 16},
    ),
    dict(
        id="cutfile",
        label="Cut file",
        description="An output format, not a quality setting: shapes that never overlap and no filters, for "
                    "plotters and cutting machines. Auto never picks it.",
        sample="cutfile.png",
        params={"shadows": False, "strokes": False, "layering": "cutout", "detail": 10.0},
    ),
]

PRESETS: list[Preset] = [Preset(engine="vexel", detail=_DETAIL.get(p["id"], _UNMEASURED), **p) for p in _PRESETS]


def all_presets() -> list[Preset]:
    return list(PRESETS)


def fixed_presets() -> list[Preset]:
    """The presets that are one parameter bundle each (everything but Auto)."""
    return [p for p in PRESETS if p.kind == "preset"]


def auto_candidates() -> list[Preset]:
    """What Auto traces with, in preference order (a tie goes to the earlier)."""
    return [p for p in PRESETS if p.auto_candidate]
