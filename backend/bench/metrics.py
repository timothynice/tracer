"""Fidelity metrics. Every function is pure: arrays in, numbers out.

Colour metrics operate on RGB composited over white; alpha is scored separately
so transparent logos are judged on both shape and transparency.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.feature import canny
from skimage.metrics import structural_similarity
from skimage.morphology import dilation, disk

from bench.config import DEFAULT_WEIGHTS, Weights
from bench.raster import luminance, to_rgb_on_white
from studi0trace.imaging.svg import svg_stats


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ssim(a_rgb: np.ndarray, b_rgb: np.ndarray) -> float:
    win = min(7, a_rgb.shape[0], a_rgb.shape[1])
    win = win if win % 2 == 1 else win - 1
    return float(structural_similarity(a_rgb, b_rgb, channel_axis=-1, data_range=255, win_size=win))


def delta_e_map(a_rgb: np.ndarray, b_rgb: np.ndarray) -> np.ndarray:
    """CIEDE2000 per pixel as a float64 (H, W) array."""
    lab_a = rgb2lab(a_rgb.astype(np.float64) / 255.0)
    lab_b = rgb2lab(b_rgb.astype(np.float64) / 255.0)
    return deltaE_ciede2000(lab_a, lab_b)


def delta_e(a_rgb: np.ndarray, b_rgb: np.ndarray) -> tuple[float, float]:
    """CIEDE2000 per pixel → (mean, 95th percentile)."""
    de = delta_e_map(a_rgb, b_rgb)
    return float(de.mean()), float(np.percentile(de, 95))


def edge_f1(a_rgb: np.ndarray, b_rgb: np.ndarray, tolerance_px: int = 2, sigma: float = 1.0) -> float:
    """Canny edges of both, matched within `tolerance_px`. F1 of precision/recall."""
    ea = canny(luminance(a_rgb) / 255.0, sigma=sigma)
    eb = canny(luminance(b_rgb) / 255.0, sigma=sigma)
    na, nb = int(ea.sum()), int(eb.sum())
    if na == 0 and nb == 0:
        return 1.0
    if na == 0 or nb == 0:
        return 0.0
    footprint = disk(tolerance_px)
    precision = float((eb & dilation(ea, footprint)).sum()) / nb
    recall = float((ea & dilation(eb, footprint)).sum()) / na
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def alpha_mae(a_rgba: np.ndarray, b_rgba: np.ndarray) -> float:
    return float(np.abs(a_rgba[..., 3].astype(np.float32) - b_rgba[..., 3].astype(np.float32)).mean() / 255.0)


def smooth_mask(src_rgb: np.ndarray, window: int = 7, lo: float = 0.5, hi: float = 6.0) -> np.ndarray:
    """Pixels where the source varies gently: a gradient, not a flat fill or an edge."""
    lum = luminance(src_rgb)
    mean = ndimage.uniform_filter(lum, window, mode="reflect")
    mean_sq = ndimage.uniform_filter(lum * lum, window, mode="reflect")
    std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
    return (std > lo) & (std < hi)


def banding_index(src_rgb: np.ndarray, out_rgb: np.ndarray) -> tuple[float, float]:
    """How much more stair-stepped the output is than the source, over the
    source's smooth-gradient regions. Returns (index, smooth_fraction).

    index = mean over the mask of max(0, |∇²L_out| − |∇²L_src|). A faithful
    gradient scores ~0; a posterised one scores in the tens.
    """
    mask = smooth_mask(src_rgb)
    fraction = float(mask.mean())
    if not mask.any():
        return 0.0, fraction
    lap_src = np.abs(ndimage.laplace(luminance(src_rgb)))
    lap_out = np.abs(ndimage.laplace(luminance(out_rgb)))
    excess = np.maximum(lap_out - lap_src, 0.0)
    return float(excess[mask].mean()), fraction


def composite(raw: dict, weights: Weights = DEFAULT_WEIGHTS) -> dict[str, float]:
    fidelity = (
        weights.w_ssim * _clamp01(raw["ssim"])
        + weights.w_delta_e * (1.0 - _clamp01(raw["delta_e_mean"] / weights.delta_e_scale))
        + weights.w_edge * _clamp01(raw["edge_f1"])
    )
    smoothness = 1.0 - _clamp01(raw["banding_index"] / weights.banding_scale)
    economy = _clamp01(1.0 - math.log10(max(raw["paths"], 1)) / weights.economy_log_span)
    score = weights.s_fidelity * fidelity + weights.s_smooth * smoothness + weights.s_economy * economy
    return {"fidelity": fidelity, "smoothness": smoothness, "economy": economy, "score": score}


def all_metrics(
    src_rgba: np.ndarray,
    out_rgba: np.ndarray,
    svg: str,
    elapsed_ms: float,
    truth_paths: int | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> dict:
    src_rgb = to_rgb_on_white(src_rgba)
    out_rgb = to_rgb_on_white(out_rgba)
    de_mean, de_p95 = delta_e(src_rgb, out_rgb)
    banding, smooth_fraction = banding_index(src_rgb, out_rgb)
    stats = svg_stats(svg).as_dict()
    raw = {
        "ssim": ssim(src_rgb, out_rgb),
        "delta_e_mean": de_mean,
        "delta_e_p95": de_p95,
        "edge_f1": edge_f1(src_rgb, out_rgb),
        "alpha_mae": alpha_mae(src_rgba, out_rgba),
        "banding_index": banding,
        "smooth_fraction": smooth_fraction,
        **stats,
        "path_ratio": (stats["paths"] / truth_paths) if truth_paths else None,
        "elapsed_ms": elapsed_ms,
    }
    return {**raw, **composite(raw, weights)}
