"""Bench configuration: corpus classes and composite-score weights.

The composite is a convenience for ranking. Raw metrics are always reported
alongside it, and every results.json echoes the weights it was scored with.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

CLASSES: tuple[str, ...] = ("logo", "flat", "gradient", "shadow")


@dataclass(frozen=True)
class Weights:
    # fidelity = w_ssim*ssim + w_delta_e*(1 - clamp(delta_e_mean / delta_e_scale)) + w_edge*edge_f1
    w_ssim: float = 0.35
    w_delta_e: float = 0.35
    w_edge: float = 0.30
    delta_e_scale: float = 20.0
    # smoothness = 1 - clamp(banding_index / banding_scale)
    banding_scale: float = 8.0
    # economy = clamp(1 - log10(max(paths, 1)) / economy_log_span)   1 path → 1.0, 10^span paths → 0
    economy_log_span: float = 4.0
    # score = s_fidelity*fidelity + s_smooth*smoothness + s_economy*economy
    s_fidelity: float = 0.60
    s_smooth: float = 0.25
    s_economy: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


DEFAULT_WEIGHTS = Weights()
