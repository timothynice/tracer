"""Auto: trace an image every way worth trying and keep the cleanest of the most faithful.

A user cannot tell which preset suits their image, and each preset has its own
class of artifact, so Auto does not guess: it traces the image with every
preset marked `auto_candidate` (Balanced, Logo & icon, Detailed, Simplified),
scores each trace by fidelity (mean ΔE and edge F1 against the source) and by
cleanliness (the scorecard's artifact index), and picks with one rule that
can be said in a line:

    among the candidates whose ΔE is within max(DE_SLACK, DE_SHARE × best) of
    the most faithful one's and whose edge F1 is within EDGE_SLACK of the best
    of those, take the one with the lowest artifact index; a tie goes to fewer
    shapes, then to the earlier preset.

"As faithful as the best, and the cleanest of those." Flat & poster and Cut
file are never candidates: they are a style and an output format, which the
user chooses; they are not a quality trade-off Auto can make for them.

Everything here is synchronous and pure. The API runs the candidates
concurrently (`api/routes.py`); the Rust engine releases the GIL while it
traces. `bench.presets_eval` applies the same `choose` to its records, so the
Auto line in the preset list is measured with the rule that ships.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from studi0trace.engines.base import TraceInput
from studi0trace.engines.presets import Preset, auto_candidates
from studi0trace.imaging import quality

DE_SLACK = 0.15    # ΔE a candidate may give away to the most faithful one…
DE_SHARE = 0.30    # …or this share of the best ΔE, whichever is larger
EDGE_SLACK = 0.02  # edge F1 it may give away to the best of those


@dataclass
class Scored:
    """What `choose` needs to know about one candidate."""
    id: str
    delta_e: float
    edge_f1: float
    artifact_index: float
    elements: int


def de_limit(best: float) -> float:
    return best + max(DE_SLACK, DE_SHARE * best)


def faithful(scored: list[Scored]) -> list[Scored]:
    """The candidates as faithful as the best, in the order given."""
    if not scored:
        return []
    limit = de_limit(min(s.delta_e for s in scored))
    ok = [s for s in scored if s.delta_e <= limit]
    best_edge = max(s.edge_f1 for s in ok)
    return [s for s in ok if s.edge_f1 >= best_edge - EDGE_SLACK]


def _key(s: Scored, order: dict[str, int]) -> tuple:
    return (round(s.artifact_index, 1), s.elements, order.get(s.id, 0))


def choose(scored: list[Scored]) -> tuple[Scored | None, str]:
    """The pick and why, in words that finish 'Auto chose <preset> — …'."""
    if not scored:
        return None, "no candidate could be scored"
    order = {s.id: i for i, s in enumerate(scored)}
    ok = faithful(scored)
    pick = min(ok, key=lambda s: _key(s, order))
    most_faithful = min(scored, key=lambda s: (s.delta_e, order[s.id]))
    cleanest = min(scored, key=lambda s: _key(s, order))
    others = [s for s in ok if s is not pick]
    if len(scored) == 1:
        return pick, "the only candidate that traced"
    if len(ok) == 1:
        return pick, "the only one this faithful to the image"
    if pick is cleanest and pick is most_faithful:
        return pick, "the most faithful, and the cleanest"
    if any(round(s.artifact_index, 1) == round(pick.artifact_index, 1) for s in others):
        return pick, "as clean at the same fidelity, with fewer shapes"
    if pick is cleanest:
        return pick, "the cleanest at the same fidelity"
    if pick is most_faithful:
        return pick, "the most faithful; the cleaner ones lose detail"
    return pick, "the cleanest of the most faithful"


def issues(card: dict) -> list[str]:
    """What is visibly wrong with a trace, in a designer's words, worst first."""
    out: list[str] = []
    if card["pinholes"]:
        out.append(f"{card['pinholes']} pinhole{'s' if card['pinholes'] != 1 else ''}")
    thin = card["slivers"] + card["degenerate"] + card["thin_strokes"]
    if thin:
        out.append(f"{thin} sliver{'s' if thin != 1 else ''}")
    if card["wobble_deg_100px"] >= 25.0:
        out.append("wobbly edges")
    uneven = card["radius_inconsistent"] + card["rect_bowed"] + card.get("rect_skewed", 0)
    if uneven:
        out.append(f"{uneven} uneven rectangle{'s' if uneven != 1 else ''}")
    if card["inflections"] >= 3:
        out.append("wavy curves")
    return out


def summary(scores: dict) -> dict[str, Any]:
    """The per-candidate scores the API returns."""
    return {
        "delta_e": round(float(scores["delta_e_mean"]), 4),
        "edge_f1": round(float(scores["edge_f1"]), 4),
        "artifact_index": round(float(scores["artifact_index"]), 2),
        "clean": quality.is_clean(scores),
        "issues": issues(scores),
        "shapes": int(scores["elements"]),
        "pinholes": int(scores["pinholes"]),
        "slivers": int(scores["slivers"] + scores["degenerate"] + scores["thin_strokes"]),
        "wobble": round(float(scores["wobble_deg_100px"]), 1),
        "inflections": int(scores["inflections"]),
        "uneven_rects": int(scores["radius_inconsistent"] + scores["rect_bowed"] + scores.get("rect_skewed", 0)),
    }


def reference(image: TraceInput) -> quality.Reference:
    return quality.Reference(np.asarray(image.image.convert("RGBA")))


def assess(svg: str, ref: quality.Reference) -> dict:
    return quality.assess(svg, ref)


def candidates(engine_id: str) -> list[Preset]:
    return [p for p in auto_candidates() if p.engine == engine_id]
