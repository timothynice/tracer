"""Named parameter bundles.

Each preset is a *trade-off*, not a secret better setting: the defaults already
win on every bench class, so a preset earns its place by moving the balance
between fidelity, path count, speed and portability in a direction someone
actually wants.

Every number quoted in a `detail` line was measured over the whole bench corpus
(52 synthetic items plus the real logos) with `python -m bench run`, against the
same corpus and the same engine build. Re-measure them when the engine changes;
a preset that quotes a stale number is worse than one that quotes none.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Preset(BaseModel):
    id: str
    label: str
    engine: str
    description: str = Field(description="What this preset is for, in the user's terms")
    detail: str = Field(description="What it measurably costs or buys, from the bench")
    sample: str = Field(description="Thumbnail filename under /presets/")
    params: dict[str, Any]


# ΔE and path counts are corpus means; lower ΔE is closer to the original.
PRESETS: list[Preset] = [
    Preset(
        id="balanced",
        label="Balanced",
        engine="vexel",
        description="Gradients, shadows, strokes and overlaps all reconstructed. The right starting point for most artwork.",
        detail="ΔE 0.54 · 6 paths · 5 KB",
        sample="balanced.png",
        params={},
    ),
    Preset(
        id="logo",
        label="Logo & icon",
        engine="vexel",
        description="Merges harder and fits whole shapes, for a small, clean, hand-editable file. Best where the mark matters more than the last half-pixel.",
        detail="ΔE 0.67 · 5 paths · 3 KB — 45% fewer nodes",
        sample="logo.png",
        params={"detail": 10.0, "min_region": 16, "curve_tolerance": 0.6, "corner_threshold": 70.0},
    ),
    Preset(
        id="detailed",
        label="Detailed illustration",
        engine="vexel",
        description="Keeps more regions and more gradient stops. For dense art where subtle colour shifts matter.",
        detail="ΔE 0.53 — the closest match, at 2.4× the nodes",
        sample="detailed.png",
        params={"detail": 3.5, "min_region": 3, "max_stops": 6, "curve_tolerance": 0.25},
    ),
    Preset(
        id="flat",
        label="Flat & poster",
        engine="vexel",
        description="Solid colours only — no gradients, no filters. A style choice, and the safest thing to hand to an old importer.",
        detail="ΔE 1.47 · 12 paths — visibly posterised, by design",
        sample="flat.png",
        params={"gradients": False, "shadows": False, "detail": 14.0, "min_region": 16},
    ),
    Preset(
        id="dense",
        label="Photo & dense art",
        engine="vexel",
        description="Skips stroke and overlap recovery and merges aggressively. For photographic or very busy images, where those stages cost time and find nothing.",
        detail="ΔE 0.76 · 5 paths — about 3.5× faster",
        sample="dense.png",
        params={"detail": 14.0, "min_region": 24, "strokes": False, "overlaps": False, "shadows": False},
    ),
    Preset(
        id="cutfile",
        label="Cut file",
        engine="vexel",
        description="Non-overlapping shapes and no filters, for plotters and cutting machines that ignore anything clever.",
        detail="ΔE 0.67 · 7 paths · every shape a closed outline",
        sample="cutfile.png",
        params={"shadows": False, "strokes": False, "layering": "cutout", "detail": 10.0},
    ),
]


def all_presets() -> list[Preset]:
    return list(PRESETS)
