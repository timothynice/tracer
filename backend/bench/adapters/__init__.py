"""Bench-only engines: competitors the bench compares Vexel against, and Vexel's Auto.

These register into the same engine registry the service uses, but only when
the bench imports this package (`load()`); `studi0trace.engines.registry.load_builtin`
never does, so nothing here is a runtime dependency of the app, and none of
them appear in `GET /engines`.

  autotrace     the `autotrace` CLI (brew install autotrace), colour mode
  imagetracer   ImageTracer.js under Node (npm install imagetracerjs pngjs),
                found through $IMAGETRACER_DIR (the npm project holding
                node_modules) or NODE_PATH
  vexel-auto    Vexel's Auto preset: every Auto candidate traced and scored,
                and `studi0trace.auto.choose` picks, exactly as the API does

An adapter whose tool is not installed is left unregistered; `load()` says
which, and `bench run` drops it from the run with that message instead of
recording an error on every item.

`CONFIGS` is the documented set of configurations the head-to-head runs for
each competitor: its defaults, and a small set of its own documented presets
(or, for AutoTrace, which has none, a grid of its two main knobs). The best
of them is chosen by mean composite score on the held-out corpus
(`bench/heldout`), never on the development corpus. Vexel is run at its
defaults and as Auto only.
"""
from __future__ import annotations

from studi0trace.engines import registry

_REASONS: dict[str, str] = {}


def load() -> dict[str, str]:
    """Register every adapter whose tool is present. Returns {engine id: why it is missing}."""
    from bench.adapters import autotrace, imagetracer, vexel_auto

    registry.load_builtin()
    _REASONS.clear()
    for mod in (autotrace, imagetracer, vexel_auto):
        engine = mod.ENGINE
        why = engine.missing()
        if why is None:
            registry.register(engine)
        else:
            registry.unregister(engine.id)
            _REASONS[engine.id] = why
    return dict(_REASONS)


def missing() -> dict[str, str]:
    return dict(_REASONS)


# VTracer's CLI presets (`vtracer --preset bw|poster|photo`, `Config::from_preset`
# in visioncortex/vtracer cmdapp/src/config.rs). Only the two colour presets are
# run: `bw` is 1-bit, which Potrace covers. path_precision 2 is what the presets set.
VTRACER_PRESETS = {
    "poster": dict(colormode="color", hierarchical="stacked", filter_speckle=4, color_precision=8,
                   layer_difference=16, mode="spline", corner_threshold=60, length_threshold=4.0,
                   splice_threshold=45, max_iterations=10, path_precision=2),
    "photo": dict(colormode="color", hierarchical="stacked", filter_speckle=10, color_precision=8,
                  layer_difference=48, mode="spline", corner_threshold=180, length_threshold=4.0,
                  splice_threshold=45, max_iterations=10, path_precision=2),
}

# ImageTracer.js 1.2.6's built-in option presets (ImageTracer.optionpresets), all of them.
IMAGETRACER_PRESETS = ("default", "posterized1", "posterized2", "posterized3", "curvy", "sharp", "detailed",
                       "smoothed", "grayscale", "fixedpalette", "randomsampling1", "randomsampling2",
                       "artistic1", "artistic2", "artistic3", "artistic4")

# AutoTrace has no presets; its colour count and despeckle level are the two
# settings its documentation leads with. Everything else stays at its default.
AUTOTRACE_GRID = [(c, d) for c in (8, 16, 32, 64) for d in (0, 4)]

# name -> (engine id, params). "<engine>" alone is that engine's defaults.
CONFIGS: dict[str, tuple[str, dict]] = {
    "vexel": ("vexel", {}),
    "vexel-auto": ("vexel-auto", {}),
    "potrace": ("potrace", {}),
    "vtracer": ("vtracer", {}),
    **{f"vtracer-{k}": ("vtracer", v) for k, v in VTRACER_PRESETS.items()},
    "autotrace": ("autotrace", {}),
    **{f"autotrace-c{c}-d{d}": ("autotrace", {"color_count": c, "despeckle_level": d}) for c, d in AUTOTRACE_GRID},
    "imagetracer": ("imagetracer", {}),
    **{f"imagetracer-{p}": ("imagetracer", {"preset": p}) for p in IMAGETRACER_PRESETS if p != "default"},
}

# Which configurations are one competitor's candidates for "best preset".
FAMILIES: dict[str, list[str]] = {}
for _name, (_eid, _p) in CONFIGS.items():
    FAMILIES.setdefault(_eid, []).append(_name)
