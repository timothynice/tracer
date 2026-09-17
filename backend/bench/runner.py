"""Run engines over a corpus and write results.json (+ media.json for the report)."""
from __future__ import annotations

import base64
import io
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from bench import metrics
from bench.config import DEFAULT_WEIGHTS, Weights
from bench.corpus import Item
from bench.raster import load_png, rasterize, to_rgb_on_white
from studi0trace.engines import registry
from studi0trace.imaging.intake import load_upload

NUMERIC_KEYS = (
    "ssim", "delta_e_mean", "delta_e_p95", "edge_f1", "alpha_mae", "banding_index", "smooth_fraction",
    "paths", "nodes", "bytes", "gradients", "unique_fills", "path_ratio", "elapsed_ms",
    "fidelity", "smoothness", "economy", "score",
)
THUMB = 160


def _png_b64(rgba: np.ndarray, size: int = THUMB) -> str:
    img = Image.fromarray(rgba, "RGBA")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _heatmap(de: np.ndarray, scale: float = 30.0) -> np.ndarray:
    """ΔE → white (0) → amber → red (≥ scale), RGBA."""
    t = np.clip(de / scale, 0.0, 1.0)
    r = np.full_like(t, 255.0)
    g = 255.0 * (1.0 - 0.75 * t)
    b = 255.0 * (1.0 - t) ** 2
    a = np.full_like(t, 255.0)
    return np.dstack([r, g, b, a]).astype(np.uint8)


def score_item(item: Item, engine_id: str, params: dict, weights: Weights, media: dict | None) -> dict:
    engine = registry.get(engine_id)
    record: dict = {"id": item.id, "cls": item.cls, "engine": engine_id}
    src_rgba = load_png(item.png)
    try:
        image = load_upload(item.png.read_bytes(), max_bytes=1 << 30, max_pixels=1 << 30)
        result = engine.trace(image, engine.Params.model_validate(params))
        out_rgba = rasterize(result.svg, item.width, item.height)
    except Exception as exc:  # noqa: BLE001 - a failing engine is a data point, not a crash
        record["error"] = f"{type(exc).__name__}: {exc}"
        if media is not None:
            media[f"{item.id}|{engine_id}"] = {"src": _png_b64(src_rgba)}
        return record

    m = metrics.all_metrics(src_rgba, out_rgba, result.svg, result.elapsed_ms, item.truth_paths, weights)
    record["metrics"] = m
    if media is not None:
        de = metrics.delta_e_map(to_rgb_on_white(src_rgba), to_rgb_on_white(out_rgba))
        media[f"{item.id}|{engine_id}"] = {
            "src": _png_b64(src_rgba),
            "out": _png_b64(out_rgba),
            "heat": _png_b64(_heatmap(de)),
            "svg_bytes": m["bytes"],
        }
    return record


def summarize(records: list[dict]) -> dict:
    """summary[engine][cls][metric] = mean over items that produced that metric."""
    buckets: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"items": 0, "errors": 0}))
    for r in records:
        c = counts[r["engine"]][r["cls"]]
        c["items"] += 1
        if "error" in r:
            c["errors"] += 1
            continue
        for k in NUMERIC_KEYS:
            v = r["metrics"].get(k)
            if v is not None:
                buckets[r["engine"]][r["cls"]][k].append(float(v))
    summary: dict = {}
    for engine, by_cls in counts.items():
        summary[engine] = {}
        for cls, c in by_cls.items():
            means = {k: float(np.mean(v)) for k, v in buckets[engine][cls].items()}
            summary[engine][cls] = {**means, **c}
    return summary


def run(
    items: list[Item],
    engine_ids: list[str],
    params: dict[str, dict] | None = None,
    label: str = "run",
    out_dir: Path | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
    media: bool = True,
) -> Path:
    """Score every engine on every item. Returns the results.json path."""
    from bench.report import write_html  # local import: report needs no engines

    registry.load_builtin()
    params = params or {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_dir or Path("bench/reports") / f"{stamp}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    media_store: dict | None = {} if media else None
    records: list[dict] = []
    started = time.perf_counter()
    for item in items:
        for eid in engine_ids:
            records.append(score_item(item, eid, params.get(eid, {}), weights, media_store))

    results = {
        "label": label,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": time.perf_counter() - started,
        "weights": weights.as_dict(),
        "engines": {eid: {"params": registry.get(eid).Params.model_validate(params.get(eid, {})).model_dump()} for eid in engine_ids},
        "items": records,
        "summary": summarize(records),
    }
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    if media_store is not None:
        (out_dir / "media.json").write_text(json.dumps(media_store), encoding="utf-8")
    write_html(results, out_dir / "index.html", media_store)
    return results_path


def load_results(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(a: dict, b: dict, tolerance: float = 0.01, metric: str = "score") -> tuple[list[str], bool]:
    """Per engine/class deltas of `metric` (b − a). Regressed if any drops by > tolerance."""
    lines: list[str] = []
    regressed = False
    for engine, by_cls in sorted(b["summary"].items()):
        base = a["summary"].get(engine, {})
        for cls, stats in sorted(by_cls.items()):
            if metric not in stats or cls not in base or metric not in base[cls]:
                lines.append(f"{engine:>10} {cls:<9} {metric}: {stats.get(metric, float('nan')):.4f}  (no baseline)")
                continue
            delta = stats[metric] - base[cls][metric]
            flag = ""
            if delta < -tolerance:
                flag = "  REGRESSION"
                regressed = True
            elif delta > tolerance:
                flag = "  improved"
            lines.append(f"{engine:>10} {cls:<9} {metric}: {base[cls][metric]:.4f} → {stats[metric]:.4f}  ({delta:+.4f}){flag}")
    return lines, regressed
