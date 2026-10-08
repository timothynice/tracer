"""Degraded bench set: vector truth, damaged the way real uploads are.

Thirteen sources with vector truth (synthetic corpus templates, Studi0's own
marks, two held-out emoji and a geometric stand-in for a wave lockup) are
rendered with resvg and put through four degradations:

  nn2x     rendered at half size and doubled nearest-neighbour; columns pair at
           phase 0, rows at phase 0 (even source index) or 1 (odd index)
  sharpen  rendered at full size, then an unsharp mask: a dark rim inside every
           edge and a light halo outside it
  small    rendered with the long side at 176 px: strokes under 2 px
  combo    half size, unsharp mask, ±1-level ground noise, doubled with rows
           at phase 1: the whole signature of an upscaled, sharpened asset

`python -m bench.degraded generate` writes bench/degraded byte for byte the
same on every run; `python -m bench run --corpus bench/degraded` scores it.
The data directory bench/degraded/ must never get an __init__.py: this
module and that directory share a name, and the module wins only while the
directory is not a package.
"""
from __future__ import annotations

import argparse
import io
import math
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image, ImageFilter
from scipy import ndimage

from bench.corpus import Item, write_manifest

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "degraded"
CLASSES = ("nn2x", "sharpen", "small", "combo")
SMALL_SIDE = 176
UNSHARP = {"radius": 1.5, "percent": 120, "threshold": 0}
NOISE_SIGMA = 40.0  # px at the final size

# --- rendering --------------------------------------------------------------------------

_ROOT = re.compile(r"<svg\b[^>]*>")
_CANVAS_ATTR = re.compile(r"""\s(?:width|height|viewBox|preserveAspectRatio)\s*=\s*(?:"[^"]*"|'[^']*')""")


def viewbox(svg: str) -> tuple[float, float, float, float]:
    """The root element's viewBox as (min-x, min-y, width, height)."""
    tag = _ROOT.search(svg).group(0)
    m = re.search(r"""viewBox\s*=\s*["']([^"']*)["']""", tag)
    if m is None:
        raise ValueError("source SVG has no viewBox")
    x0, y0, vw, vh = (float(v) for v in re.split(r"[\s,]+", m.group(1).strip()))
    return x0, y0, vw, vh


def fit(svg: str, long_side: int) -> tuple[int, int]:
    """(width, height) with the long side `long_side`, the short side rounded half up."""
    _, _, vw, vh = viewbox(svg)
    k = long_side / max(vw, vh)
    return int(math.floor(vw * k + 0.5)), int(math.floor(vh * k + 0.5))


def render(svg: str, width: int, height: int, row_phase: int = 0) -> np.ndarray:
    """RGBA uint8, exactly `height` × `width`, the viewBox stretched onto the
    canvas (the root's width, height and preserveAspectRatio are replaced).

    row_phase 1 moves the pixel grid half a row up and adds a row (height + 1
    rows): `nn2x(render(svg, w, h, 1), 1)` then lands every native row on the
    two output rows it covers, with rows paired as (2k+1, 2k+2)."""
    x0, y0, vw, vh = viewbox(svg)
    rows = height
    if row_phase:
        step = vh / height
        y0, vh, rows = y0 - step / 2, vh + step, height + 1
    tag = _ROOT.search(svg)
    root = _CANVAS_ATTR.sub("", tag.group(0))[:-1].rstrip()
    root += (f' width="{width}" height="{rows}" viewBox="{x0!r} {y0!r} {vw!r} {vh!r}"'
             ' preserveAspectRatio="none">')
    png = resvg_py.svg_to_bytes(svg_string=svg[:tag.start()] + root + svg[tag.end():], skip_system_fonts=True)
    rgba = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"), dtype=np.uint8)
    if rgba.shape[:2] != (rows, width):
        raise ValueError(f"resvg rendered {rgba.shape[1]}x{rgba.shape[0]}, not {width}x{rows}")
    return rgba


# --- degradations: pure functions over RGBA arrays -------------------------------------

def nn2x(native: np.ndarray, row_phase: int) -> np.ndarray:
    """Nearest-neighbour 2×. Columns pair at phase 0. Rows pair at phase 0, or
    at phase 1: then `native` carries one extra row (render(..., row_phase=1)),
    the first output row is dropped, rows (2k+1, 2k+2) are equal, row 0 and
    the last row stand alone, and the output is 2·(rows − 1) high."""
    up = np.repeat(np.repeat(native, 2, axis=0), 2, axis=1)
    if row_phase:
        up = up[1:1 + 2 * (native.shape[0] - 1)]
    return np.ascontiguousarray(up)


def unsharp(rgba: np.ndarray) -> np.ndarray:
    """Pillow's UnsharpMask(radius=1.5, percent=120, threshold=0) on RGB; alpha kept."""
    rgb = Image.fromarray(np.ascontiguousarray(rgba[..., :3]), "RGB").filter(ImageFilter.UnsharpMask(**UNSHARP))
    out = rgba.copy()
    out[..., :3] = np.asarray(rgb, dtype=np.uint8)
    return out


def ground_noise(rgba: np.ndarray, rng: random.Random, sigma: float) -> np.ndarray:
    """A ±1-level blotch field — white noise blurred by a Gaussian of `sigma`
    px, scaled to peak at 1 and rounded — added to R, G and B of the opaque
    pixels (alpha 255) only."""
    noise = np.random.default_rng(rng.getrandbits(64)).standard_normal(rgba.shape[:2])
    field = ndimage.gaussian_filter(noise, sigma, mode="reflect")
    peak = float(np.abs(field).max())
    delta = np.rint(field / peak).astype(np.int16) if peak > 0 else np.zeros(rgba.shape[:2], np.int16)
    out = rgba.copy()
    opaque = rgba[..., 3] == 255
    rgb = out[..., :3].astype(np.int16)
    rgb[opaque] += delta[opaque][:, None]
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def degrade(cls: str, svg: str, long_side: int, row_phase: int, rng: random.Random) -> np.ndarray:
    """`svg` through one class's degradation; `long_side` is the final long
    side (176 for small), `row_phase` the nn2x row phase (combo always uses 1)."""
    if cls == "nn2x":
        return nn2x(render(svg, *fit(svg, long_side // 2), row_phase=row_phase), row_phase)
    if cls == "sharpen":
        return unsharp(render(svg, *fit(svg, long_side)))
    if cls == "small":
        return render(svg, *fit(svg, long_side))
    if cls == "combo":
        native = unsharp(render(svg, *fit(svg, long_side // 2), row_phase=1))
        # The noise goes on at native size (σ halved) so that the doubling stays
        # exact: the asset's blotches are doubled with the rest of it.
        return nn2x(ground_noise(native, rng, NOISE_SIGMA / 2), 1)
    raise ValueError(f"unknown class {cls!r}")


def encode_png(rgba: np.ndarray) -> bytes:
    """PNG bytes through Pillow (optimize=True), like `synth.render_png`."""
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --- the wave-lockup stand-in -----------------------------------------------------------
# Abstract geometry that keeps the engine stresses (soft-ended acute ribbon tips, a thin
# channel, 4-stop gradients, thin strokes, heavy stems, tiny counters and acute vertices)
# and resembles no real mark: no font, no letter, no word, no palette or layout of any logo.

WAVE_W, WAVE_H = 1208, 308
WAVE_BACKGROUND = "#fefefe"
SHAPE_COLOUR = "#2b2b2b"  # the thin and the heavy shapes
SMALL_COLOUR = "#e0703a"  # the row of small shapes
LIGHT_STOPS = ((0.0, "#d8913a"), (0.35, "#e8c65c"), (0.7, "#a0c04f"), (1.0, "#7ac75f"))
DARK_STOPS = ((0.0, "#4a1430"), (0.45, "#d93b7f"), (0.7, "#b02a5f"), (1.0, "#4a1430"))
CHANNEL = 7.25  # vertical px between the two ribbons: 6-7 px of background once the antialiased rims are counted
LIGHT_TIPS = (110, 408)  # local x of the light ribbon's two tips
DARK_TIPS = (34, 327)    # local x of the dark ribbon's two tips (the far one is where _ribbons ends its top edge)
DARK_LEFT_TIP = (34, 206)
DARK_BOTTOM = ((291, 150), (232, 194), (172, 176), (120, 152), (70, 170))  # far tip -> left tip
MIRROR = 1196  # the ribbons are drawn on the left in a local frame and mirrored onto the right


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _pt(p: tuple[float, float]) -> str:
    return f"{_f(p[0])} {_f(p[1])}"


def _lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _split(p0, p1, p2, p3, t: float):
    """de Casteljau: the cubic's piece from 0 to t, as four points."""
    a, b, c = _lerp(p0, p1, t), _lerp(p1, p2, t), _lerp(p2, p3, t)
    d, e = _lerp(a, b, t), _lerp(b, c, t)
    return p0, a, d, _lerp(d, e, t)


def _mirror_pairs(d: str) -> str:
    """A path of absolute M/C/Z commands reflected about x = MIRROR / 2 (x -> MIRROR - x)."""
    toks = re.findall(r"[MCZ]|-?\d+(?:\.\d+)?", d)
    out, k = [], 0
    for t in toks:
        if t in "MCZ":
            out.append(t)
        else:
            out.append(_f(MIRROR - float(t)) if k % 2 == 0 else _f(float(t)))
            k += 1
    return " ".join(out).replace("M ", "M").replace("C ", "C").replace(" Z", " Z")


def _ribbons() -> tuple[str, str]:
    """(light, dark) path data: two tapered ribbons, pointed at both ends, flowing
    right to left (drawn in a local frame, then mirrored onto the right of the canvas)."""
    top = [((110, 142), (146, 112), (190, 52), (244, 56)), ((244, 56), (304, 60), (350, 112), (408, 86))]
    bottom = [((408, 86), (344, 140), (292, 134), (232, 108)), ((232, 108), (190, 92), (155, 120), (110, 142))]
    light = f"M{_pt(top[0][0])}" + "".join(f" C{_pt(s[1])} {_pt(s[2])} {_pt(s[3])}" for s in top + bottom) + " Z"
    # The dark ribbon's top edge is the light one's bottom edge, CHANNEL px lower,
    # from x 110 to the dark ribbon's far tip at t = 0.55 along the first piece.
    s1 = [(x, y + CHANNEL) for x, y in bottom[0]]  # (408,93.25) -> (232,115.25)
    s2 = [(x, y + CHANNEL) for x, y in bottom[1]]  # (232,115.25) -> (110,149.25)
    r1 = _split(s1[3], s1[2], s1[1], s1[0], 0.55)  # (232,115.25) -> the far tip
    dark = (f"M{_pt(DARK_LEFT_TIP)} C56 186 83 162 {_pt(s2[3])}"
            f" C{_pt(s2[2])} {_pt(s2[1])} {_pt(s2[0])}"
            f" C{_pt(r1[1])} {_pt(r1[2])} {_pt(r1[3])}"
            f" C{_pt(DARK_BOTTOM[0])} {_pt(DARK_BOTTOM[1])} {_pt(DARK_BOTTOM[2])}"
            f" C{_pt(DARK_BOTTOM[3])} {_pt(DARK_BOTTOM[4])} {_pt(DARK_LEFT_TIP)} Z")
    return _mirror_pairs(light), _mirror_pairs(dark)


def _stroked(d: str, width: float, colour: str = SHAPE_COLOUR) -> str:
    return f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{_f(width)}"/>'


def _thin_row() -> str:
    """Five thin shapes (6 px strokes), unevenly spaced, y 100-180: a ring, a wide
    shallow arc, a sideways wave, a horizontal bar with a dot below its end, a plus."""
    return "".join([
        f'<circle cx="90" cy="140" r="36" fill="none" stroke="{SHAPE_COLOUR}" stroke-width="6"/>',
        _stroked("M160 166 A60 60 0 0 1 270 166", 6),
        _stroked("M322 144 C340 98 372 98 377 144 S412 190 432 142", 6),
        f'<rect x="486" y="116" width="84" height="6" fill="{SHAPE_COLOUR}"/>',
        f'<circle cx="567" cy="152" r="7" fill="{SHAPE_COLOUR}"/>',
        _stroked("M640 102 V178 M602 140 H678", 6),
    ])


def _heavy_row() -> str:
    """Four heavy shapes (16 px stems), y 205-285: two bars of different heights, a
    chevron whose inner notch is acute, a bar into a ring, and a corner that opens
    up and to the left."""
    c = SHAPE_COLOUR
    return "".join([
        f'<rect x="48" y="205" width="16" height="80" fill="{c}"/>',
        f'<rect x="96" y="245" width="16" height="40" fill="{c}"/>',
        f'<path d="M170 205 H190 L274 245 L190 285 H170 L254 245 Z" fill="{c}"/>',
        f'<rect x="340" y="237" width="56" height="16" fill="{c}"/>',
        f'<circle cx="418" cy="245" r="30" fill="none" stroke="{c}" stroke-width="16"/>',
        f'<path d="M584 205 H600 V285 H520 V269 H584 Z" fill="{c}"/>',
    ])


def _small_shape(i: int, x: float) -> str:
    """Small shape i of ten, 20 px tall (y 40-60) and about as wide, 3.5 px strokes."""
    cx, cy, r, w = x + 10, 50.0, 8.25, 3.5
    ring = lambda rr: f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(rr)}"/>'
    p = lambda d: f'<path d="{d}"/>'
    return [
        ring(r),                                                                             # ring
        p(f"M{_f(cx)} {_f(cy - r)} A{_f(r)} {_f(r)} 0 1 1 {_f(cx - r)} {_f(cy)}"),           # three-quarter arc
        p(f"M{_f(x)} 50 L{_f(x + 5)} 41 L{_f(x + 10)} 59 L{_f(x + 15)} 41 L{_f(x + 20)} 50"),  # zigzag, 3 turns
        p(f"M{_f(cx)} 41.5 L{_f(x + 19)} 58.25 H{_f(x + 1)} Z"),                              # triangle with a counter
        f'<rect x="{_f(x + 1.75)}" y="41.75" width="16.5" height="16.5"/>',                  # square ring
        p(f"M{_f(x + 3)} 41.75 L{_f(x + 17)} 50 L{_f(x + 3)} 58.25"),                        # chevron
        p(f"M{_f(cx)} 40 V60 M{_f(x)} 50 H{_f(x + 20)}"),                                    # plus sign
        p(f"M{_f(x)} 50 H{_f(x + 14)}"),                                                     # short bar
        ring(r) + ring(3.5),                                                                 # double ring
        p(f"M{_f(cx)} 40.8 L{_f(x + 17)} 50 L{_f(cx)} 59.2 L{_f(x + 3)} 50 Z"),              # diamond
    ][i]


# diamond, zigzag, double ring, chevron, triangle, plus, ring, short bar, arc, square ring
SMALL_ORDER = (9, 2, 8, 5, 3, 6, 0, 7, 1, 4)
SMALL_XS = (48, 112, 170, 249, 301, 388, 437, 520, 566, 655)  # uneven on purpose


def _small_row() -> str:
    body = "".join(_small_shape(i, x) for i, x in zip(SMALL_ORDER, SMALL_XS))
    return (f'<g fill="none" stroke="{SMALL_COLOUR}" stroke-width="3.5" stroke-linecap="butt" '
            f'stroke-linejoin="miter">{body}</g>')


def _gradient(gid: str, x1: float, x2: float, stops) -> str:
    body = "".join(f'<stop offset="{_f(o)}" stop-color="{c}"/>' for o, c in stops)
    return (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_f(x1)}" y1="0" x2="{_f(x2)}" y2="0">'
            f"{body}</linearGradient>")


def wave_lockup_svg() -> str:
    """The wave-lockup stand-in: two tapered gradient ribbons on the right with a
    7 px channel between them, flowing right to left; on the left a row of ten small
    shapes above a row of five thin shapes and a row of four heavy ones, all abstract
    geometry, on #fefefe. viewBox 0 0 1208 308."""
    light, dark = _ribbons()
    defs = (_gradient("light", MIRROR - LIGHT_TIPS[0], MIRROR - LIGHT_TIPS[1], LIGHT_STOPS)
            + _gradient("dark", MIRROR - DARK_TIPS[0], MIRROR - DARK_TIPS[1], DARK_STOPS))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WAVE_W} {WAVE_H}"><defs>{defs}</defs>'
            f'<rect width="{WAVE_W}" height="{WAVE_H}" fill="{WAVE_BACKGROUND}"/>'
            f'<path d="{dark}" fill="url(#dark)"/><path d="{light}" fill="url(#light)"/>'
            f'{_small_row()}<g fill="{SHAPE_COLOUR}">{_thin_row()}{_heavy_row()}</g></svg>')


# --- the set ----------------------------------------------------------------------------

OWN = "Studi0 (this repository)"


@dataclass(frozen=True)
class Source:
    name: str
    origin: str                      # corpus | heldout | synthetic: the source:<origin> tag
    path: str | None                 # relative to backend/bench; None for the generated stand-in
    long_side: int = 512             # final long side of nn2x, sharpen and combo
    licence: str = OWN               # the SOURCES.md licence line
    licence_file: str | None = None  # file in bench/heldout/LICENSES copied beside the set


NOTO = "Apache-2.0, Copyright Google LLC and the Noto Emoji authors, Google Noto Emoji (commit 06121655d0e8; see ../heldout/SOURCES.md)"
FLUENT = "MIT, Copyright (c) Microsoft Corporation, Fluent Emoji (commit 1ffb34c752ec; see ../heldout/SOURCES.md)"

SOURCES: tuple[Source, ...] = (
    Source("thin-mark", "corpus", "corpus/synthetic/logo/thin-mark.svg"),
    Source("wedge-fan", "corpus", "corpus/synthetic/logo/wedge-fan.svg"),
    Source("venn", "corpus", "corpus/synthetic/logo/venn.svg"),
    Source("hex-nest", "corpus", "corpus/synthetic/logo/hex-nest.svg"),
    Source("linear-4stop", "corpus", "corpus/synthetic/gradient/linear-4stop.svg"),
    Source("radial-disc", "corpus", "corpus/synthetic/gradient/radial-disc.svg"),
    Source("sticker", "corpus", "corpus/synthetic/flat/sticker.svg"),
    Source("card", "corpus", "corpus/synthetic/shadow/card.svg"),
    Source("logomark", "corpus", "corpus/real/logo/logomark.svg"),
    Source("studi0trace-mark", "corpus", "corpus/real/logo/studi0trace-mark.svg"),
    Source("u2049", "heldout", "heldout/noto/u2049.svg", licence=NOTO, licence_file="Apache-2.0.txt"),
    Source("nail-polish", "heldout", "heldout/fluent-color/nail-polish.svg", licence=FLUENT,
           licence_file="MIT-fluentui-emoji.txt"),
    Source("wave-lockup", "synthetic", None, long_side=WAVE_W),
)


def source_svg(src: Source) -> str:
    return wave_lockup_svg() if src.path is None else (HERE / src.path).read_text(encoding="utf-8")


def write_sources(root: Path) -> Path:
    """SOURCES.md: where every truth SVG comes from and the licence it is under."""
    lines = [
        "# Degraded bench set: sources and licences", "",
        "Written by `python -m bench.degraded generate` (`backend/bench/degraded.py`); do not edit by hand.",
        "Each `<class>/<source>.svg` is a byte-for-byte copy of the source below (the stand-in is",
        "generated), and each PNG beside it is a render of that SVG, damaged as its class says. The",
        "held-out sources and their renders are covered by the licence texts in `LICENSES/`.", "",
        "`sharpen` (and so `combo`) shows its dark rim inside and light halo outside only against an opaque",
        "ground: on a transparent ground the unsharp mask over straight RGB brightens the rim instead.", "",
        "| source | origin | truth | licence |", "|---|---|---|---|",
    ]
    for s in SOURCES:
        where = f"`bench/{s.path}`" if s.path else "generated by `wave_lockup_svg()`"
        lines.append(f"| {s.name} | {s.origin} | {where} | {s.licence} |")
    path = root / "SOURCES.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate(root: Path = DEFAULT_OUT, seed: int = 1234) -> list[Item]:
    """Write every class × source under root and rewrite root/manifest.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    items: list[Item] = []
    for cls in CLASSES:
        out_dir = root / cls
        out_dir.mkdir(parents=True, exist_ok=True)
        for index, src in enumerate(SOURCES):
            svg = source_svg(src)
            truth = out_dir / f"{src.name}.svg"
            if src.path is None:
                truth.write_text(svg, encoding="utf-8")
            else:
                shutil.copyfile(HERE / src.path, truth)
            size = SMALL_SIDE if cls == "small" else src.long_side
            row_phase = index % 2 if cls == "nn2x" else 1
            rgba = degrade(cls, svg, size, row_phase, random.Random(f"{seed}:{cls}:{src.name}"))
            png = out_dir / f"{src.name}-{size}.png"
            png.write_bytes(encode_png(rgba))
            items.append(Item(
                id=f"{cls}/{src.name}-{size}", cls=cls, png=png, width=rgba.shape[1], height=rgba.shape[0],
                truth_svg=truth, tags=["degraded", f"degraded:{cls}", f"source:{src.origin}", f"size:{size}"],
            ))
    licences = root / "LICENSES"
    licences.mkdir(exist_ok=True)
    for name in sorted({s.licence_file for s in SOURCES if s.licence_file}):
        shutil.copyfile(HERE / "heldout" / "LICENSES" / name, licences / name)
    write_sources(root)
    write_manifest(root, items)
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.degraded")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="(re)build the degraded set")
    g.add_argument("--out", default=str(DEFAULT_OUT))
    g.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args(argv)
    items = generate(Path(args.out), seed=args.seed)
    print(f"generated {len(items)} items under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
