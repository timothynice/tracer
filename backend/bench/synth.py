"""Deterministic synthetic corpus.

Each template is an SVG on a 512×512 canvas rendered through resvg at the
requested sizes, so the corpus is licence-free, reproducible byte-for-byte for
a given seed, and carries exact vector ground truth.

Classes mirror Vexel's targets:
  logo      solid shapes, sharp geometry, few colours, transparent background
  flat      many adjacent flat regions with low-contrast neighbours and small details
  gradient  linear/radial multi-stop gradients, including alpha stops
  shadow    feGaussianBlur drop shadows and glows at several radii
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Callable

import resvg_py
from PIL import Image

from bench.corpus import Item, load_corpus, write_manifest

CANVAS = 512
NS = 'xmlns="http://www.w3.org/2000/svg"'

BRAND = ["#1F2A44", "#F5C518", "#E63946", "#2A9D8F", "#264653", "#F4A261", "#457B9D", "#8338EC", "#06D6A0", "#EF476F"]
PASTEL = ["#FFD6E0", "#C1FBA4", "#7BF1A8", "#9BF6FF", "#A0C4FF", "#BDB2FF", "#FFC6FF", "#FDFFB6", "#CAFFBF", "#FFADAD"]


def _svg(body: str, defs: str = "", background: str | None = None) -> str:
    bg = f'<rect width="{CANVAS}" height="{CANVAS}" fill="{background}"/>' if background else ""
    defs_block = f"<defs>{defs}</defs>" if defs else ""
    return f'<svg {NS} viewBox="0 0 {CANVAS} {CANVAS}">{defs_block}{bg}{body}</svg>'


def _pick(rng: random.Random, palette: list[str], n: int) -> list[str]:
    return rng.sample(palette, n)


def _poly(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _regular(cx: float, cy: float, r: float, n: int, rot: float = 0.0) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n), cy + r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]


# --- logo ---------------------------------------------------------------------


def logo_ring(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        f'<circle cx="256" cy="256" r="200" fill="{a}"/>'
        f'<circle cx="256" cy="256" r="120" fill="{b}"/>'
        f'<circle cx="256" cy="256" r="60" fill="{a}"/>'
    )


def logo_cutout(rng):
    a, = _pick(rng, BRAND, 1)
    return _svg(
        f'<path fill-rule="evenodd" fill="{a}" d="M96 64 h320 a32 32 0 0 1 32 32 v320 a32 32 0 0 1 -32 32 h-320 '
        f'a32 32 0 0 1 -32 -32 v-320 a32 32 0 0 1 32 -32 z M256 150 a106 106 0 1 0 0.1 0 z"/>'
    )


def logo_triangle_bar(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        f'<polygon points="{_poly([(256, 60), (452, 400), (60, 400)])}" fill="{a}"/>'
        f'<rect x="120" y="420" width="272" height="36" rx="6" fill="{b}"/>'
    )


def logo_hex_nest(rng):
    a, b, c = _pick(rng, BRAND, 3)
    return _svg(
        f'<polygon points="{_poly(_regular(256, 256, 220, 6, math.pi / 6))}" fill="{a}"/>'
        f'<polygon points="{_poly(_regular(256, 256, 150, 6, math.pi / 6))}" fill="{b}"/>'
        f'<polygon points="{_poly(_regular(256, 256, 70, 3, -math.pi / 2))}" fill="{c}"/>'
    )


def logo_venn(rng):
    a, b, c = _pick(rng, BRAND, 3)
    return _svg(
        f'<circle cx="200" cy="210" r="130" fill="{a}" opacity="0.9"/>'
        f'<circle cx="312" cy="210" r="130" fill="{b}" opacity="0.9"/>'
        f'<circle cx="256" cy="310" r="130" fill="{c}" opacity="0.9"/>'
    )


def logo_thin_mark(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        f'<circle cx="256" cy="256" r="190" fill="none" stroke="{a}" stroke-width="6"/>'
        f'<circle cx="256" cy="256" r="150" fill="none" stroke="{a}" stroke-width="2"/>'
        f'<path d="M160 300 L256 140 L352 300 Z" fill="none" stroke="{b}" stroke-width="10" stroke-linejoin="round"/>'
        f'<line x1="256" y1="140" x2="256" y2="372" stroke="{b}" stroke-width="3"/>'
    )


# --- flat ---------------------------------------------------------------------


def _blob(rng, cx, cy, r) -> str:
    pts = []
    n = rng.randint(6, 10)
    for i in range(n):
        ang = 2 * math.pi * i / n
        rr = r * rng.uniform(0.7, 1.15)
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f} "
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        d += f"Q{p0[0]:.1f},{p0[1]:.1f} {mx:.1f},{my:.1f} "
    return d + "Z"


def flat_blobs(rng):
    bg = rng.choice(PASTEL)
    body = ""
    for _ in range(rng.randint(6, 9)):
        c = rng.choice(BRAND + PASTEL)
        body += f'<path d="{_blob(rng, rng.uniform(100, 412), rng.uniform(100, 412), rng.uniform(60, 140))}" fill="{c}"/>'
    for _ in range(rng.randint(3, 6)):
        body += f'<circle cx="{rng.uniform(40, 472):.0f}" cy="{rng.uniform(40, 472):.0f}" r="{rng.uniform(4, 9):.0f}" fill="{rng.choice(BRAND)}"/>'
    return _svg(body, background=bg)


def flat_stripes(rng):
    cols = _pick(rng, PASTEL, 4)
    body = "".join(f'<rect x="0" y="{i * 128}" width="512" height="128" fill="{cols[i]}"/>' for i in range(4))
    a, b = _pick(rng, BRAND, 2)
    body += f'<circle cx="180" cy="256" r="120" fill="{a}"/><rect x="290" y="150" width="160" height="212" rx="24" fill="{b}"/>'
    return _svg(body)


def flat_low_contrast(rng):
    # neighbours differ by a few RGB units: a quantiser will merge them
    base = rng.randint(80, 160)
    body = ""
    for i in range(4):
        for j in range(4):
            r, g, b = base + 6 * i, base + 4 * j, base + 3 * (i + j)
            body += f'<rect x="{i * 128}" y="{j * 128}" width="128" height="128" fill="rgb({r},{g},{b})"/>'
    body += f'<circle cx="256" cy="256" r="90" fill="{rng.choice(BRAND)}"/>'
    return _svg(body)


def flat_sticker(rng):
    a, b, c = _pick(rng, BRAND, 3)
    return _svg(
        f'<path d="{_blob(rng, 256, 256, 200)}" fill="#FFFFFF"/>'
        f'<path d="{_blob(rng, 256, 256, 170)}" fill="{a}"/>'
        f'<ellipse cx="200" cy="230" rx="30" ry="40" fill="#FFFFFF"/><ellipse cx="312" cy="230" rx="30" ry="40" fill="#FFFFFF"/>'
        f'<circle cx="208" cy="240" r="14" fill="{b}"/><circle cx="320" cy="240" r="14" fill="{b}"/>'
        f'<path d="M180 320 Q256 380 332 320" fill="none" stroke="{c}" stroke-width="14" stroke-linecap="round"/>'
    )


def flat_mosaic(rng):
    body = ""
    for i in range(8):
        for j in range(8):
            if rng.random() < 0.55:
                body += f'<rect x="{i * 64}" y="{j * 64}" width="64" height="64" fill="{rng.choice(BRAND)}"/>'
    return _svg(body, background=rng.choice(PASTEL))


def flat_overlap(rng):
    cols = _pick(rng, BRAND, 5)
    body = ""
    for k, c in enumerate(cols):
        pts = _regular(200 + 30 * k, 220 + 25 * k, 140, rng.randint(3, 7), rng.uniform(0, math.pi))
        body += f'<polygon points="{_poly(pts)}" fill="{c}"/>'
    return _svg(body, background="#FFFFFF")


# --- gradient -----------------------------------------------------------------


def _stops(colors: list[str], opacities: list[float] | None = None) -> str:
    n = len(colors)
    out = ""
    for i, c in enumerate(colors):
        op = f' stop-opacity="{opacities[i]}"' if opacities else ""
        out += f'<stop offset="{i / (n - 1):.3f}" stop-color="{c}"{op}/>'
    return out


def grad_linear_frame(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        f'<rect width="512" height="512" fill="url(#g)"/>',
        defs=f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="0">{_stops([a, b])}</linearGradient>',
    )


def grad_radial_disc(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        '<circle cx="256" cy="256" r="220" fill="url(#g)"/>',
        defs=f'<radialGradient id="g">{_stops([a, b])}</radialGradient>',
    )


def grad_linear_4stop(rng):
    cols = _pick(rng, BRAND, 4)
    return _svg(
        '<rect x="56" y="56" width="400" height="400" rx="48" fill="url(#g)"/>',
        defs=f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">{_stops(cols)}</linearGradient>',
    )


def grad_multi_shape(rng):
    c1 = _pick(rng, BRAND, 2)
    c2 = _pick(rng, BRAND, 2)
    c3 = _pick(rng, BRAND, 3)
    return _svg(
        '<circle cx="170" cy="170" r="130" fill="url(#a)"/>'
        '<rect x="260" y="60" width="200" height="200" rx="20" fill="url(#b)"/>'
        f'<polygon points="{_poly([(256, 290), (460, 470), (52, 470)])}" fill="url(#c)"/>',
        defs=(
            f'<radialGradient id="a">{_stops(c1)}</radialGradient>'
            f'<linearGradient id="b" x1="0" y1="0" x2="0" y2="1">{_stops(c2)}</linearGradient>'
            f'<linearGradient id="c" x1="0" y1="1" x2="1" y2="0">{_stops(c3)}</linearGradient>'
        ),
    )


def grad_radial_focal(rng):
    cols = _pick(rng, BRAND, 3)
    return _svg(
        '<rect width="512" height="512" fill="url(#g)"/>',
        defs=f'<radialGradient id="g" cx="0.5" cy="0.5" r="0.7" fx="0.3" fy="0.3">{_stops(cols)}</radialGradient>',
    )


def grad_alpha_fade(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        '<rect x="40" y="120" width="432" height="272" rx="30" fill="url(#g)"/>'
        f'<circle cx="256" cy="256" r="70" fill="{b}"/>',
        defs=f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="0">{_stops([a, a, b], [1.0, 0.0, 1.0])}</linearGradient>',
    )


# --- shadow -------------------------------------------------------------------


def _shadow_filter(fid: str, std: float, dx: float = 0, dy: float = 0, opacity: float = 0.45) -> str:
    return (
        f'<filter id="{fid}" x="-50%" y="-50%" width="200%" height="200%">'
        f'<feGaussianBlur in="SourceAlpha" stdDeviation="{std}"/>'
        f'<feOffset dx="{dx}" dy="{dy}" result="o"/>'
        f'<feComponentTransfer in="o" result="s"><feFuncA type="linear" slope="{opacity}"/></feComponentTransfer>'
        f'<feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>'
        f"</filter>"
    )


def shadow_disc(rng):
    a, = _pick(rng, BRAND, 1)
    return _svg(f'<circle cx="256" cy="236" r="150" fill="{a}" filter="url(#f)"/>', defs=_shadow_filter("f", 8, 0, 18), background="#FFFFFF")


def shadow_card(rng):
    a, = _pick(rng, BRAND, 1)
    return _svg(
        f'<rect x="96" y="96" width="320" height="300" rx="24" fill="{a}" filter="url(#f)"/>'
        f'<rect x="130" y="130" width="252" height="40" rx="8" fill="#FFFFFF" opacity="0.85"/>',
        defs=_shadow_filter("f", 16, 0, 24),
        background="#F3F4F6",
    )


def shadow_over_gradient(rng):
    a, b, c = _pick(rng, BRAND, 3)
    return _svg(
        f'<circle cx="190" cy="230" r="110" fill="{c}" filter="url(#f)"/>'
        f'<rect x="270" y="150" width="180" height="180" rx="30" fill="#FFFFFF" filter="url(#f)"/>',
        defs=f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">{_stops([a, b])}</linearGradient>' + _shadow_filter("f", 10, 0, 14),
        background="url(#g)",
    )


def shadow_glow(rng):
    a, b = _pick(rng, BRAND, 2)
    return _svg(
        f'<circle cx="256" cy="256" r="170" fill="{a}"/>'
        f'<circle cx="256" cy="256" r="110" fill="{b}" filter="url(#blur)"/>',
        defs='<filter id="blur"><feGaussianBlur stdDeviation="22"/></filter>',
        background="#111827",
    )


def shadow_radii(rng):
    cols = _pick(rng, BRAND, 3)
    return _svg(
        f'<rect x="40" y="120" width="120" height="120" rx="16" fill="{cols[0]}" filter="url(#s4)"/>'
        f'<rect x="196" y="120" width="120" height="120" rx="16" fill="{cols[1]}" filter="url(#s12)"/>'
        f'<rect x="352" y="120" width="120" height="120" rx="16" fill="{cols[2]}" filter="url(#s24)"/>'
        f'<circle cx="256" cy="380" r="70" fill="{cols[1]}" filter="url(#s12)"/>',
        defs=_shadow_filter("s4", 4, 0, 6) + _shadow_filter("s12", 12, 0, 14) + _shadow_filter("s24", 24, 0, 26),
        background="#FFFFFF",
    )


def shadow_transparent_bg(rng):
    a, = _pick(rng, BRAND, 1)
    return _svg(
        f'<polygon points="{_poly(_regular(256, 250, 170, 5, -math.pi / 2))}" fill="{a}" filter="url(#f)"/>',
        defs=_shadow_filter("f", 12, 0, 20, 0.5),
    )


TEMPLATES: dict[str, list[tuple[str, Callable[[random.Random], str]]]] = {
    "logo": [
        ("ring", logo_ring), ("cutout", logo_cutout), ("triangle-bar", logo_triangle_bar),
        ("hex-nest", logo_hex_nest), ("venn", logo_venn), ("thin-mark", logo_thin_mark),
    ],
    "flat": [
        ("blobs", flat_blobs), ("stripes", flat_stripes), ("low-contrast", flat_low_contrast),
        ("sticker", flat_sticker), ("mosaic", flat_mosaic), ("overlap", flat_overlap),
    ],
    "gradient": [
        ("linear-frame", grad_linear_frame), ("radial-disc", grad_radial_disc), ("linear-4stop", grad_linear_4stop),
        ("multi-shape", grad_multi_shape), ("radial-focal", grad_radial_focal), ("alpha-fade", grad_alpha_fade),
    ],
    "shadow": [
        ("disc", shadow_disc), ("card", shadow_card), ("over-gradient", shadow_over_gradient),
        ("glow", shadow_glow), ("radii", shadow_radii), ("transparent-bg", shadow_transparent_bg),
    ],
}


def render_png(svg: str, size: int) -> bytes:
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))
    # Re-encode through Pillow so the bytes are stable across resvg versions.
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def generate(root: Path, seed: int = 1234, sizes: tuple[int, ...] = (512, 128)) -> list[Item]:
    """Write the synthetic corpus under root/synthetic and rewrite the manifest.

    Existing manifest entries whose PNG lives outside `synthetic/` (hand-added
    real images) are preserved.
    """
    root.mkdir(parents=True, exist_ok=True)
    kept: list[Item] = []
    if (root / "manifest.yaml").exists():
        kept = [i for i in load_corpus(root) if "synthetic" not in i.png.relative_to(root).parts]

    items: list[Item] = []
    for cls, templates in TEMPLATES.items():
        out_dir = root / "synthetic" / cls
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, fn in templates:
            rng = random.Random(f"{seed}:{cls}:{name}")
            svg = fn(rng)
            svg_path = out_dir / f"{name}.svg"
            svg_path.write_text(svg, encoding="utf-8")
            for size in sizes:
                png_path = out_dir / f"{name}-{size}.png"
                png_path.write_bytes(render_png(svg, size))
                items.append(Item(
                    id=f"{cls}/{name}-{size}", cls=cls, png=png_path, width=size, height=size,
                    truth_svg=svg_path, tags=["synthetic", f"size:{size}"],
                ))
    write_manifest(root, kept + items)
    return items
