"""Golden fixtures for crates/studi0trace-core, exported from the Python reference.

    .venv/bin/python -m tools.export_core_fixtures            # everything
    .venv/bin/python -m tools.export_core_fixtures --only schema,presets

Each exporter writes one JSON file into the crate's tests/fixtures. The Rust
tests compare against them, so the Python stays the definition of what the
core does until the Python server is retired.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "crates" / "studi0trace-core" / "tests" / "fixtures"
EXPORTERS: dict[str, Callable[[], None]] = {}


def exporter(name: str):
    def wrap(fn):
        EXPORTERS[name] = fn
        return fn
    return wrap


def write(name: str, data, *, sort_keys: bool = True) -> None:
    """One fixture. Keys are sorted so a re-export diffs cleanly; a fixture that holds the ORDER of a
    response's keys (`api`) says `sort_keys=False` and carries it as written."""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=sort_keys) + "\n", encoding="utf-8")


@exporter("schema")
def _schema() -> None:
    from studi0trace.engines.vexel.engine import VexelParams

    # `write` sorts keys, so the order the UI lays its controls out in (the order the
    # fields are declared, which Pydantic keeps in `properties`) is carried explicitly.
    write("schema", {
        "schema": VexelParams.model_json_schema(),
        "defaults": VexelParams().model_dump(),
        "order": list(VexelParams.model_fields),
    })


@exporter("presets")
def _presets() -> None:
    from studi0trace.engines.presets import all_presets

    # `GET /presets` as the API sends it, `detail` lines and all.
    write("presets", [p.model_dump() for p in all_presets()])


@exporter("intake")
def _intake() -> None:
    import hashlib
    import io
    import struct
    import zlib

    from PIL import Image

    from studi0trace.imaging.intake import IntakeError, load_upload

    big = 1 << 30
    src = Image.open(ROOT / "backend/bench/corpus/real/logo/vexel-wordmark-512.png").convert("RGBA").resize((96, 96))

    def save(image, fmt: str, file: str, **kw) -> bytes:
        buf = io.BytesIO()
        image.save(buf, fmt, **kw)
        (OUT / file).write_bytes(buf.getvalue())
        return buf.getvalue()

    def describe(data: bytes, file: str, *, lossless: bool = True) -> dict:
        img = load_upload(data, max_bytes=big, max_pixels=big)
        rgba = img.image.tobytes()
        return {
            "file": file,
            "lossless": lossless,
            "format": img.source_format,
            "width": img.width,
            "height": img.height,
            "sha256": hashlib.sha256(rgba).hexdigest(),
            "rgba_sum": sum(rgba),
            # Position-weighted, so two images with the same pixels in another order differ.
            "rgba_weighted": sum(b * (i % 251 + 1) for i, b in enumerate(rgba)),
        }

    cases: dict = {}
    for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp"), ("BMP", "bmp")):
        file = f"intake_{ext}.{ext}"
        data = save(src.convert("RGB") if fmt in ("JPEG", "BMP") else src, fmt, file, **({"quality": 90} if fmt == "JPEG" else {}))
        cases[ext] = describe(data, file, lossless=fmt != "JPEG")
        if fmt == "JPEG":
            # Raw RGBA as Pillow decodes it, so the test can measure the difference byte by byte.
            (OUT / "intake_jpg.rgba").write_bytes(load_upload(data, max_bytes=big, max_pixels=big).image.tobytes())

    # The wordmark is opaque everywhere, so transparency needs a source of its own: a ramp from
    # clear to opaque across the width, and a fully clear block whose colour must survive.
    import numpy as np

    px = np.array(src)
    px[:, :, 3] = np.linspace(0, 255, 96).astype(np.uint8)[None, :]
    px[:24, :24, 3] = 0
    alpha_src = Image.fromarray(px, "RGBA")
    for ext, fmt, kw in (("png", "PNG", {}), ("gif", "GIF", {"transparency": 0}), ("webp", "WEBP", {"lossless": True})):
        file = f"intake_alpha.{ext}"
        image = alpha_src.convert("P", palette=Image.Palette.ADAPTIVE, colors=64) if fmt == "GIF" else alpha_src
        cases[f"alpha_{ext}"] = describe(save(image, fmt, file, **kw), file)

    # Palette with transparency (tRNS), grey, grey+alpha: modes Pillow widens to RGBA its own way.
    pal = alpha_src.quantize(colors=48, method=Image.Quantize.FASTOCTREE)
    for key, image in (("png_pal", pal), ("png_l", src.convert("L")), ("png_la", alpha_src.convert("LA"))):
        file = f"intake_{key}.png"
        cases[key] = describe(save(image, "PNG", file), file)

    # Animated files: Pillow takes the first frame, and the test pins that the Rust does too.
    def frame(colour, corner) -> Image.Image:
        im = Image.new("RGBA", (32, 32), colour)
        im.paste((255, 255, 255, 255), (corner, corner, corner + 8, corner + 8))
        return im

    f1, f2 = frame((200, 30, 30, 255), 2), frame((30, 120, 200, 255), 20)
    for fmt, ext, kw in (("GIF", "gif", {"duration": 100, "loop": 0}), ("WEBP", "webp", {"duration": 100, "loop": 0, "lossless": True})):
        file = f"intake_anim.{ext}"
        data = save(f1, fmt, file, save_all=True, append_images=[f2], **kw)
        with Image.open(io.BytesIO(data)) as probe:
            assert getattr(probe, "n_frames", 1) == 2, f"{fmt}: expected a two-frame file"
        first = describe(data, file)
        with Image.open(io.BytesIO(data)) as probe:
            probe.seek(1)
            second = probe.convert("RGBA").tobytes()
        assert second != load_upload(data, max_bytes=big, max_pixels=big).image.tobytes(), f"{fmt}: the frames must differ for the test to pin the first"
        cases[f"anim_{ext}"] = first

    # EXIF orientation 6 (rotate 90° clockwise to display): a 40x20 JPEG that must come out 20x40
    exif = Image.Exif()
    exif[0x0112] = 6
    data = save(Image.new("RGB", (40, 20), (200, 30, 30)), "JPEG", "intake_exif6.jpg", exif=exif.tobytes())
    out = load_upload(data, max_bytes=big, max_pixels=big)
    cases["exif6"] = {"file": "intake_exif6.jpg", "width": out.width, "height": out.height}
    assert (out.width, out.height) == (20, 40)

    # 16-bit-per-channel PNGs, built by hand (Pillow cannot write 16-bit RGB or grey+alpha),
    # 16x16, opening with the samples where rounding and scaling rules part company: 0x0182
    # (high byte 1, (c+128)/257 says 2), 0x80FF (128; Pillow's grey path clips it to 255).
    edge = [0x0000, 0x0001, 0x007F, 0x00FF, 0x0100, 0x0182, 0x0183, 0x7FFF, 0x8000, 0x80FF, 0x8100, 0xFEFF, 0xFF00, 0xFF7F, 0xFF80, 0xFFFF]

    def png16(colour_type: int, channels: int) -> tuple[bytes, list[int]]:
        n = 16 * 16 * channels
        samples = (edge + [(i * 40503 + 0x0182) & 0xFFFF for i in range(n)])[:n]
        rows = b"".join(
            b"\0" + b"".join(struct.pack(">H", samples[(y * 16 + x) * channels + c]) for x in range(16) for c in range(channels))
            for y in range(16)
        )

        def chunk(tag: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

        head = struct.pack(">IIBBBBB", 16, 16, 16, colour_type, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""), samples

    cases["bit16"] = {}
    for key, colour_type, channels in (("grey", 0, 1), ("rgb", 2, 3), ("la", 4, 2), ("rgba", 6, 4)):
        data, samples = png16(colour_type, channels)
        file = f"intake_16_{key}.png"
        (OUT / file).write_bytes(data)
        pillow = list(load_upload(data, max_bytes=big, max_pixels=big).image.tobytes())
        if key == "grey":
            # Not Pillow's decode: Pillow reads 16-bit grey as I;16 and clips it to 255 (0x80FF -> 255,
            # everything above the darkest 0.4% turns white). The intake deliberately keeps the high byte instead.
            want = [v for s in samples for v in ((s >> 8),) * 3 + (255,)]
            assert want != pillow, "Pillow was expected to clip 16-bit grey"
            basis = "high byte of the source samples; deliberately not Pillow's clipped decode"
        else:
            # Pillow takes the high byte of every channel; the exporter asserts that, so the
            # Rust is held to Pillow and to the rule in one.
            want = pillow
            high = [s >> 8 for s in samples]
            if key == "rgb":
                assert pillow == [v for i in range(256) for v in high[i * 3:i * 3 + 3] + [255]]
            elif key == "la":
                assert pillow == [v for i in range(256) for v in (high[i * 2],) * 3 + (high[i * 2 + 1],)]
            else:
                assert pillow == high
            basis = "Pillow's decode (the high byte of every channel)"
        cases["bit16"][key] = {"file": file, "width": 16, "height": 16, "basis": basis, "rgba_hex": bytes(want).hex()}

    # JPEGs that must load although they look odd, and the damaged ones Pillow refuses.
    jpg = (OUT / "intake_jpg.jpg").read_bytes()
    tbuf = io.BytesIO()
    src.convert("RGB").resize((8, 8)).save(tbuf, "JPEG")
    thumb = tbuf.getvalue()

    # An EXIF block carrying a complete 8x8 JPEG thumbnail (its own SOI ... SOS ... EOI), as
    # cameras write them: a check that looks for any FFD9 would take the thumbnail's for the
    # picture's. IFD0 = {Orientation: 1}, IFD1 = {JPEGInterchangeFormat, ...Length}.
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<HH", 1, 0) + struct.pack("<I", 26)
    ifd1 = struct.pack("<H", 2) + struct.pack("<HHII", 0x0201, 4, 1, 56) + struct.pack("<HHII", 0x0202, 4, 1, len(thumb)) + struct.pack("<I", 0)
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0 + ifd1 + thumb
    assert len(ifd0) == 18 and len(ifd1) == 30
    with_thumb = save(src.convert("RGB"), "JPEG", "intake_jpg_thumb.jpg", quality=90, exif=b"Exif\0\0" + tiff)
    assert with_thumb.count(b"\xff\xd8") >= 2 and with_thumb.count(b"\xff\xd9") >= 2  # two pictures in the file
    # Two pictures in one file, MPO style: the first is complete, the file goes on after its EOI.
    trailing = jpg + thumb + b"\0" * 64
    (OUT / "intake_jpg_trailing.jpg").write_bytes(trailing)

    cases["jpeg_ok"] = {}
    for key, file, data in (("thumb", "intake_jpg_thumb.jpg", with_thumb), ("trailing", "intake_jpg_trailing.jpg", trailing)):
        out = load_upload(data, max_bytes=big, max_pixels=big)
        assert (out.width, out.height) == (96, 96), key
        cases["jpeg_ok"][key] = {"file": file, "width": 96, "height": 96, "rgba_sum": sum(out.image.tobytes())}

    sos = jpg.index(b"\xff\xda")  # the first, and only, scan of a baseline file
    sos_end = sos + 2 + struct.unpack(">H", jpg[sos + 2:sos + 4])[0]
    assert jpg.endswith(b"\xff\xd9") and jpg.count(b"\xff\xda") == 1

    # Rejections: the code and the words the frontend shows, for the limits the test replays.
    png = (OUT / "intake_png.png").read_bytes()
    tiff = save(Image.new("RGB", (4, 4), (9, 9, 9)), "TIFF", "intake_tiff.tif")

    # A PNG whose header claims 10000x10000 and whose body is cut off right after it: the
    # pixel limit must answer from the header, before any decoding can fail on the body.
    ihdr = bytearray(png[8 + 8:8 + 8 + 13])
    ihdr[0:8] = struct.pack(">II", 10000, 10000)
    head = png[:8] + struct.pack(">I", 13) + b"IHDR" + bytes(ihdr) + struct.pack(">I", zlib.crc32(b"IHDR" + bytes(ihdr)))
    bomb = head + png[33:33 + 40]
    (OUT / "intake_bomb.png").write_bytes(bomb)

    def reject(data: bytes, file: str | None, max_bytes: int, max_pixels: int) -> dict:
        import warnings

        warnings.simplefilter("ignore", Image.DecompressionBombWarning)  # the bomb is the point
        try:
            load_upload(data, max_bytes=max_bytes, max_pixels=max_pixels)
        except IntakeError as exc:
            return {"file": file, "max_bytes": max_bytes, "max_pixels": max_pixels, "code": exc.code, "message": exc.message}
        raise AssertionError("expected a rejection")

    small = 20 * 1024 * 1024
    cases["errors"] = {
        "too_large": reject(png, "intake_png.png", 10, big),
        "too_large_mb": reject(b"\0" * (3 * 1024 * 1024 + 1), None, 3 * 1024 * 1024, big),
        "too_many_pixels": reject(png, "intake_png.png", big, 100),
        "header_bomb": reject(bomb, "intake_bomb.png", small, 40_000_000),
        "garbage": reject(b"not an image", None, small, 40_000_000),
        "unsupported_tiff": reject(tiff, "intake_tiff.tif", small, 40_000_000),
        "truncated_png": reject(png[: len(png) // 2], None, small, 40_000_000),
    }
    # Pillow's `load` raises "image file is truncated" for each of these; zune-jpeg, the
    # decoder behind `image`, would hand back a half-grey picture.
    for key, file, data in (
        ("truncated_jpg_half", "intake_jpg_half.jpg", jpg[: len(jpg) // 2]),
        ("truncated_jpg_after_sos", "intake_jpg_sos.jpg", jpg[:sos_end]),
        ("truncated_jpg_no_eoi", "intake_jpg_noeoi.jpg", jpg[:-2]),
        ("truncated_jpg_thumb_half", "intake_jpg_thumb_half.jpg", with_thumb[: len(with_thumb) // 2]),
    ):
        (OUT / file).write_bytes(data)
        cases["errors"][key] = reject(data, file, small, 40_000_000)
        assert cases["errors"][key]["code"] == "corrupt_image", key
    write("intake", cases)


@exporter("svg")
def _svg() -> None:
    from studi0trace.imaging.svg import normalize_dimensions, svg_stats
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload

    cases = []
    big = 1 << 30

    samples = [
        '<svg width="10pt" height="5pt" viewBox="0 0 1 1"><path d="M0 0L1 1"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"/>',
    ]

    # Add traced SVGs from corpus
    for rel in ("real/logo/vexel-wordmark-512.png", "real/logo/studi0mail-icon-512.png", "synthetic/shadow/glow-128.png"):
        png = (ROOT / "backend/bench/corpus" / rel).read_bytes()
        svg = VexelEngine().trace(load_upload(png, max_bytes=big, max_pixels=big), VexelParams()).svg
        samples.append(svg)

    # Add edge cases for regex pinning
    samples.extend([
        '<SVG WIDTH="5" HEIGHT="5" VIEWBOX="0 0 1 1"><path d="M0 0"/></SVG>',
        "<svg width='5' height='5' viewbox='0 0 1 1'><path d='M0 0'/></svg>",
        '<svg width="1" height="1" />',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path style="fill:#ABC;stroke:none" d="M0 0L4 0L4 4Z"/><path fill="none" d="M0 0L1 1"/></svg>',
        '<?xml version="1.0"?>\n<svg width="1" height="1"><path d="M0 0"/></svg>',
    ])

    for s in samples:
        cases.append({
            "svg": s,
            "normalized": normalize_dimensions(s, 64, 32),
            "stats": svg_stats(s).as_dict()
        })

    write("svg", cases)


@exporter("color")
def _color() -> None:
    import numpy as np
    from skimage.color import rgb2lab
    from skimage.color.colorconv import _cart2polar_2pi

    from studi0trace.imaging.quality import delta_e, delta_e_map, to_rgb_on_white

    def lab_of(rgb):
        return rgb2lab(rgb.astype(np.float64) / 255.0)

    rng = np.random.default_rng(7)
    # 64 random pixels, then the same nudged by up to 40 levels per channel: Lab values and
    # the CIEDE2000 of each pair, at the scale the brief's 1e-9 is about.
    rgb = rng.integers(0, 256, (64, 3), dtype=np.uint8)
    rgb2 = np.clip(rgb.astype(int) + rng.integers(-40, 41, (64, 3)), 0, 255).astype(np.uint8)
    rgba = np.concatenate([rgb, rng.integers(0, 256, (64, 1), dtype=np.uint8)], axis=1)
    mean, p95 = delta_e(rgb[None], rgb2[None])

    # Every branch of CIEDE2000 that a palette can reach: black (chroma exactly 0), near-greys
    # (chroma in the noise), identical pairs, and hues on both sides of the 0 / 2*pi wrap
    # (the `h_diff > pi` and `h_diff < -pi` corrections, each with `h_sum` above and below 2*pi).
    palette = np.array([
        (0, 0, 0), (255, 255, 255), (1, 1, 1), (64, 64, 64), (128, 128, 128), (200, 200, 200), (254, 254, 254),
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255), (255, 255, 0), (255, 128, 0),
        (255, 120, 150), (255, 100, 140), (250, 80, 110), (255, 0, 128), (200, 60, 120),
        (0, 200, 200), (40, 160, 190), (90, 190, 160), (60, 30, 120), (10, 10, 30), (250, 245, 240),
        (12, 12, 13), (240, 240, 255),
    ], dtype=np.uint8)
    n = len(palette)
    pal_lab = lab_of(palette[None])[0]
    left = np.repeat(pal_lab[:, None, :], n, axis=1)
    right = np.repeat(pal_lab[None, :, :], n, axis=0)
    cbar = 0.5 * (np.hypot(left[..., 1], left[..., 2]) + np.hypot(right[..., 1], right[..., 2]))
    scale = 1 + 0.5 * (1 - np.sqrt(cbar**7 / (cbar**7 + 25**7)))
    c1, h1 = _cart2polar_2pi(left[..., 1] * scale, left[..., 2])
    c2, h2 = _cart2polar_2pi(right[..., 1] * scale, right[..., 2])
    h_diff, h_sum, flipped = h2 - h1, h1 + h2, (c1 * c2 != 0) & (np.abs(h2 - h1) > np.pi)
    assert (c1 * c2 == 0).any() and ((h_diff > np.pi) & flipped).any() and ((h_diff < -np.pi) & flipped).any()
    assert (flipped & (h_sum < 2 * np.pi)).any() and (flipped & (h_sum >= 2 * np.pi)).any()
    pairs_a = np.repeat(palette[:, None, :], n, axis=1)   # [i, j] = palette[i] against palette[j]
    pairs_b = np.repeat(palette[None, :, :], n, axis=0)

    # to_rgb_on_white is float32 arithmetic with a +0.5 and a truncating cast: every triple of
    # channels drawn from the extremes and the values either side of the midpoint, at six
    # alphas chosen the same way, so each channel meets each (alpha, value) pair of the six.
    edge = np.array([0, 1, 127, 128, 254, 255], dtype=np.uint8)
    grid = np.array([(r, g, b, a) for a in edge for r in edge for g in edge for b in edge], dtype=np.uint8)
    # And over all 256 x 256 (alpha, value) pairs the float32 result is the exact rounding of
    # (v*a + 255*(255 - a)) / 255 (no pair is a tie), which the Rust test checks without a fixture.
    v, a = np.meshgrid(np.arange(256), np.arange(256))
    every = to_rgb_on_white(np.stack([v, v, v, a], axis=-1).astype(np.uint8))[..., 0]
    assert (every == (2 * (v * a + 255 * (255 - a)) + 255) // 510).all()

    # 64 x 64 random pairs: the mean and the 95th percentile over more than a handful of values.
    big_a = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    big_b = np.clip(big_a.astype(int) + rng.integers(-60, 61, (64, 64, 3)), 0, 255).astype(np.uint8)
    big_mean, big_p95 = delta_e(big_a, big_b)

    # np.percentile's linear method (its lerp takes the other end of the interval once the weight
    # reaches a half), on lengths that put the weight on both sides of that, with ties.
    pct = []
    for count in (1, 2, 3, 7, 20, 21, 30, 50, 100):
        values = rng.random(count) * 50 if count != 30 else rng.integers(0, 5, count) / 2.0
        pct.append({"values": values.tolist(),
                    "expect": [[q, float(np.percentile(values, q))] for q in (0, 5, 25, 50, 95, 99.9, 100)]})

    write("color", {
        "rgb": rgb.tolist(), "rgb2": rgb2.tolist(), "lab": lab_of(rgb).tolist(), "lab2": lab_of(rgb2).tolist(),
        "de": delta_e_map(rgb[None], rgb2[None])[0].tolist(), "rgba": rgba.tolist(),
        "on_white": to_rgb_on_white(rgba[None])[0].tolist(), "mean": mean, "p95": p95,
        "palette": palette.tolist(), "palette_lab": pal_lab.tolist(), "palette_de": delta_e_map(pairs_a, pairs_b).tolist(),
        "grid_rgba": grid.tobytes().hex(), "grid_on_white": to_rgb_on_white(grid[None])[0].tobytes().hex(),
        "big_a": big_a.tobytes().hex(), "big_b": big_b.tobytes().hex(), "big_mean": big_mean, "big_p95": big_p95,
        "percentiles": pct,
    })


@exporter("edges")
def _edges() -> None:
    import struct

    import numpy as np
    import scipy.ndimage as ndi
    from PIL import Image
    from scipy.ndimage._filters import _gaussian_kernel1d
    from skimage.feature import canny
    from skimage.feature._canny import _preprocess
    from skimage.feature._canny_cy import _nonmaximum_suppression_bilinear
    from skimage.morphology import dilation, disk

    from studi0trace.imaging.quality import _edge_f1, _edges, edge_f1, luminance, to_rgb_on_white

    def on(mask):
        return np.flatnonzero(mask).tolist()

    # Values the Rust must reproduce to the bit travel as their IEEE 754 bits: serde_json
    # without `float_roundtrip` can parse a decimal to the neighbouring double.
    def bits(x: float) -> str:
        return struct.pack(">d", x).hex()

    # The brief's three items (logomark-128 is a pale mark on white: no edge reaches the
    # high threshold, so it pins the empty result) and five more with thousands of edges,
    # weak ones (low-contrast) and alpha. f1 at a 3 px shift is below 1, unlike at 1 px.
    corpus = []
    for rel in ("real/logo/vexel-wordmark-512.png", "synthetic/logo/venn-128.png", "real/logo/logomark-128.png",
                "real/logo/studi0trace-mark-128.png", "synthetic/flat/low-contrast-512.png",
                "synthetic/logo/thin-mark-512.png", "synthetic/gradient/alpha-fade-128.png",
                "synthetic/logo/wedge-fan-512.png"):
        rgba = np.asarray(Image.open(ROOT / "backend/bench/corpus" / rel).convert("RGBA"))
        rgb = to_rgb_on_white(rgba)
        e = _edges(rgb)
        shifted3 = np.roll(rgb, 3, axis=1)
        corpus.append({"item": rel, "edges": on(e), "h": rgb.shape[0], "w": rgb.shape[1],
                       "f1_shifted": bits(edge_f1(rgb, np.roll(rgb, 1, axis=1))),
                       "f1_shifted3": bits(edge_f1(rgb, shifted3)),
                       "f1_shifted3_wide": bits(_edge_f1(e, _edges(shifted3), dilation(e, disk(2)), 2))})

    # canny itself on float32 images the corpus does not reach: noise on a step (weak edges
    # linked by hysteresis, near-equal magnitudes in the suppression), at three sigmas (kernel
    # radii 2, 4 and 8), and the smallest shapes (a side under 3 px has no interior at all).
    rng = np.random.default_rng(11)
    step = np.zeros((48, 64), np.float32)
    step[:, 30:] = 0.5
    step[20:, 10:40] += 0.25
    noisy = (step + rng.normal(0, 0.08, step.shape)).astype(np.float32)
    quantised = (rng.integers(0, 4, (40, 40)) / 3.0).astype(np.float32)   # exact ties everywhere
    images = {"noisy": noisy.tobytes().hex(), "quantised": quantised.tobytes().hex()}   # each sent once
    grey = []
    for name, img, sigma in (("noisy", noisy, 1.0), ("noisy", noisy, 0.5), ("noisy", noisy, 2.0),
                             ("quantised", quantised, 1.0), ("quantised", quantised, 0.7)):
        grey.append({"name": name, "h": img.shape[0], "w": img.shape[1], "sigma": bits(sigma),
                     "edges": on(canny(img, sigma=sigma))})
    for h, w in ((1, 1), (1, 7), (7, 1), (2, 2), (2, 9), (3, 3), (3, 5), (5, 4)):
        img = rng.integers(0, 2, (h, w)).astype(np.float32)
        grey.append({"name": f"tiny-{h}x{w}", "h": h, "w": w, "sigma": bits(1.0),
                     "gray": img.tobytes().hex(), "edges": on(canny(img, sigma=1.0))})
    assert any(g["edges"] for g in grey if g["name"].startswith("tiny")), "no tiny shape has an edge"

    # scipy's Gaussian taps, reversed as gaussian_filter1d hands them to correlate1d.
    kernels = [{"sigma": bits(s), "taps": [bits(t) for t in _gaussian_kernel1d(s, 0, int(4.0 * s + 0.5))[::-1].tolist()]}
               for s in (0.5, 0.7, 1.0, 2.0, 3.3)]

    # canny's float32 intermediates, which the edge maps alone do not pin: a last-bit change in
    # the smoothing's summation order or the Sobel's rarely flips an edge on these images, so
    # each stage is compared to the bit. Two 24 x 32 crops: the noisy step, and the window of
    # venn-128's luma (at sigma 0.7) with the most edges.
    venn_rgb = to_rgb_on_white(np.asarray(Image.open(ROOT / "backend/bench/corpus/synthetic/logo/venn-128.png").convert("RGBA")))
    venn, venn_edges = luminance(venn_rgb) / 255.0, _edges(venn_rgb)
    r0, c0 = max(((r, c) for r in range(0, 105, 8) for c in range(0, 97, 8)),
                 key=lambda rc: int(venn_edges[rc[0]:rc[0] + 24, rc[1]:rc[1] + 32].sum()))
    stages = []
    for name, img, sigma in (("noisy", noisy[8:32, 16:48], 1.0), ("venn", venn[r0:r0 + 24, c0:c0 + 32], 0.7)):
        img = np.ascontiguousarray(img)
        assert img.dtype == np.float32
        smoothed, eroded = _preprocess(img, None, sigma, "constant", 0.0)
        jsobel, isobel = ndi.sobel(smoothed, axis=1), ndi.sobel(smoothed, axis=0)
        magnitude = isobel * isobel
        magnitude += jsobel * jsobel
        np.sqrt(magnitude, out=magnitude)
        suppressed = _nonmaximum_suppression_bilinear(isobel, jsobel, magnitude, eroded, 0.1)
        assert all(a.dtype == np.float32 for a in (smoothed, isobel, jsobel, magnitude, suppressed))
        stages.append({"name": name, "h": img.shape[0], "w": img.shape[1], "sigma": bits(sigma),
                       "gray": img.tobytes().hex(), "smoothed": smoothed.tobytes().hex(),
                       "isobel": isobel.tobytes().hex(), "jsobel": jsobel.tobytes().hex(),
                       "magnitude": magnitude.tobytes().hex(), "suppressed": suppressed.tobytes().hex(),
                       "edges": on(canny(img, sigma=sigma))})

    # The suppression alone, on gradients of small integers and magnitudes drawn from five
    # values: interpolations that equal the centre in exact arithmetic are everywhere, so the
    # precision of each step decides (the product neigh_2 * w in float32, the rest in double).
    # Doing it all in float32, or all in double, misses 15 and 14 of these pixels.
    sup_rng = np.random.default_rng(14)
    palette = np.array([0.3, 0.7, 1.0, 1.1, 3.0], np.float32)
    si, sj = (sup_rng.integers(-5, 6, (32, 32)).astype(np.float32) for _ in range(2))
    pick = sup_rng.integers(0, 5, (32, 32))
    frame = np.zeros((32, 32), np.uint8)
    frame[1:-1, 1:-1] = 1
    kept = _nonmaximum_suppression_bilinear(si, sj, palette[pick], frame, 0.1)
    suppress = {"h": 32, "w": 32, "isobel": " ".join(str(int(v)) for v in si.ravel()),
                "jsobel": " ".join(str(int(v)) for v in sj.ravel()), "palette": [bits(float(v)) for v in palette],
                "magnitude": "".join(str(int(v)) for v in pick.ravel()), "kept": on(kept > 0)}

    # skimage's dilation with a disk: sparse random masks, pixels on the frame included.
    dilate = []
    for h, w, density in ((20, 30, 0.02), (17, 11, 0.05), (9, 40, 0.01)):
        mask = rng.random((h, w)) < density
        mask[0, 0] = mask[h - 1, w // 2] = mask[h // 2, w - 1] = True
        for r in (0, 1, 2, 3):
            dilate.append({"h": h, "w": w, "r": r, "mask": on(mask), "out": on(dilation(mask, disk(r)))})

    # _edge_f1's early returns and one ordinary value, from the Python function itself.
    def box(cells, h=12, w=12):
        m = np.zeros((h, w), bool)
        for r, c in cells:
            m[r, c] = True
        return m

    empty, a, near, far = box([]), box([(2, 2), (2, 3), (2, 4)]), box([(3, 3), (4, 4), (2, 9)]), box([(10, 10)])
    f1 = [{"name": n, "h": 12, "w": 12, "tol": t, "a": on(x), "b": on(y), "f1": bits(_edge_f1(x, y, None, t))}
          for n, x, y, t in (("both-empty", empty, empty, 2), ("a-empty", empty, a, 2), ("b-empty", a, empty, 2),
                             ("disjoint", a, far, 2), ("partial", a, near, 1), ("partial-r2", a, near, 2))]
    assert [c["f1"] for c in f1[:4]] == [bits(1.0), bits(0.0), bits(0.0), bits(0.0)]
    assert 0 < _edge_f1(a, near, None, 1) < _edge_f1(a, near, None, 2) < 1

    write("edges", {"corpus": corpus, "images": images, "grey": grey, "kernels": kernels, "stages": stages, "suppress": suppress, "dilate": dilate, "f1": f1})


@exporter("render")
def _render() -> None:
    import io

    import numpy as np
    import resvg_py
    from PIL import Image

    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload
    from studi0trace.imaging.quality import render

    # Two traced corpus items at 2x, anti-aliased and crisp: what the scorecard renders.
    names = []
    for rel in ("real/logo/vexel-wordmark-512.png", "synthetic/shadow/glow-128.png"):
        png = (ROOT / "backend/bench/corpus" / rel).read_bytes()
        img = load_upload(png, max_bytes=1 << 30, max_pixels=1 << 30)
        svg = VexelEngine().trace(img, VexelParams()).svg
        stem = Path(rel).stem
        (OUT / f"render_{stem}.svg").write_text(svg, encoding="utf-8")
        for crisp in (False, True):
            out = render(svg, img.width * 2, img.height * 2, crisp=crisp)
            Image.fromarray(out).save(OUT / f"render_{stem}_{'crisp' if crisp else 'aa'}.png")
        names.append({"stem": stem, "width": img.width * 2, "height": img.height * 2})
    write("render", names)

    # What resvg-py does with the size it is asked for, the units, shape-rendering and what it
    # refuses, on SVGs small enough to keep in a JSON file. Each case carries resvg-py's own
    # PNG (`svg_to_bytes`, before `quality.render` forces the size with Pillow) or its error.
    ns = 'xmlns="http://www.w3.org/2000/svg"'
    scene = ('<rect width="20" height="10" fill="#fafaf0"/><circle cx="5.3" cy="5.2" r="3.7" fill="#d33"/>'
             '<path d="M11 1.5 L18.2 2.3 A4 4 0 0 1 14.6 8.8 L11.4 7.6 Z" fill="#26c" fill-opacity="0.8"/>'
             '<rect x="1.25" y="0.75" width="3" height="2" rx="0.6" fill="none" stroke="#000" stroke-width="0.4"/>')
    square = '<rect width="10" height="10" fill="#c33"/><circle cx="4.6" cy="5.3" r="3.1" fill="#36c"/>'
    dot = '<rect width="5" height="5" fill="#c33"/>'
    shapes = "".join(f'<circle cx="{5.3 + 10 * i}" cy="5.3" r="4.1" fill="#d22" {a}/>' for i, a in enumerate(
        ["", 'shape-rendering="geometricPrecision"', 'shape-rendering="optimizeSpeed"', 'shape-rendering="crispEdges"']))
    cases = [
        # size: the SVG's size, rounded to whole pixels, is scaled to fit inside width x height
        ("size: 2x", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 40, 20),
        ("size: 2.5x", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 50, 25),
        ("size: 0.5x", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 10, 5),
        ("size: down to one pixel", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 1, 1),
        ("size: width and height attributes", f'<svg {ns} width="20" height="10">{scene}</svg>', 40, 20),
        ("size: attributes over a viewBox", f'<svg {ns} width="20" height="10" viewBox="0 0 10 5">{scene}</svg>', 40, 20),
        ("size: viewBox of another aspect than width x height", f'<svg {ns} width="10" height="10" viewBox="0 0 4 2">{scene}</svg>', 20, 20),
        ("size: no size at all is 100 x 100", f'<svg {ns}>{dot}</svg>', 10, 10),
        ("size: fractional 7.5 x 3.5 is 8 x 4", f'<svg {ns} width="7.5" height="3.5"><rect width="7.5" height="3.5" fill="#c33"/></svg>', 16, 8),
        ("size: fractional 7.4 x 3.6 is 7 x 4, fits 32 x 18", f'<svg {ns} width="7.4" height="3.6"><rect width="7.4" height="3.6" fill="#c33"/></svg>', 37, 18),
        ("fit: target wider than the SVG", f'<svg {ns} viewBox="0 0 10 10">{square}</svg>', 40, 20),
        ("fit: target taller than the SVG", f'<svg {ns} viewBox="0 0 10 10">{square}</svg>', 20, 40),
        ("fit: the far side rounds up", f'<svg {ns} viewBox="0 0 10 3"><rect width="10" height="3" fill="#c33"/></svg>', 30, 10),
        ("fit: a very wide target leaves one pixel", f'<svg {ns} viewBox="0 0 10 10">{square}</svg>', 100000, 1),
        # units: resvg-py leaves usvg's dpi at 0, so every absolute unit is a zero length
        ("units: px", f'<svg {ns} width="20px" height="10px">{scene}</svg>', 40, 20),
        ("units: em is 16 px", f'<svg {ns} width="1.25em" height="0.625em">{scene}</svg>', 40, 20),
        ("units: ex is half an em", f'<svg {ns} width="2.5ex" height="1.25ex">{scene}</svg>', 40, 20),
        ("units: pt is an invalid size", f'<svg {ns} width="10pt" height="5pt">{scene}</svg>', 40, 20),
        ("units: in is an invalid size", f'<svg {ns} width="1in" height="1in">{scene}</svg>', 40, 20),
        ("units: mm is an invalid size", f'<svg {ns} width="10mm" height="10mm">{scene}</svg>', 40, 20),
        # paint: everything a traced SVG uses, which has to come out of the same renderer
        ("paint: shapes, arcs and strokes", f'<svg {ns} viewBox="0 0 20 10">{scene}'
         '<path d="M2 8 A4 4 0 1 1 9 3" fill="none" stroke="#333" stroke-width="1.1" stroke-linecap="round" stroke-dasharray="1.2 0.8"/>'
         '<path d="M11 5 h6 v4 h-6 z M12.5 6 h3 v2 h-3 z" fill="#a4a" fill-rule="evenodd" stroke="#000" stroke-width="0.3" stroke-linejoin="round"/>'
         '<ellipse cx="5" cy="5" rx="2" ry="1.2" fill="#fff" transform="rotate(20 5 5)"/></svg>', 40, 20),
        ("paint: gradients", f'<svg {ns} viewBox="0 0 20 10"><defs>'
         '<linearGradient id="l" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f40"/>'
         '<stop offset="0.6" stop-color="#fc0" stop-opacity="0.7"/><stop offset="1" stop-color="#04f"/></linearGradient>'
         '<radialGradient id="r" cx="15" cy="5" r="4.3" fx="14" fy="4" gradientUnits="userSpaceOnUse" spreadMethod="reflect">'
         '<stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#306" stop-opacity="0.5"/></radialGradient></defs>'
         '<rect width="10" height="10" fill="url(#l)"/><circle cx="15" cy="5" r="4.6" fill="url(#r)"/></svg>', 40, 20),
        ("paint: opacity", f'<svg {ns} viewBox="0 0 20 10"><g opacity="0.6"><circle cx="6" cy="5" r="4" fill="#e22"/>'
         '<circle cx="9" cy="5" r="4" fill="#22e" fill-opacity="0.5"/></g>'
         '<rect x="12" y="1" width="6" height="8" fill="#0a0" opacity="0.35" stroke="#000" stroke-opacity="0.5" stroke-width="1"/></svg>', 40, 20),
        ("paint: clip and mask", f'<svg {ns} viewBox="0 0 20 10"><defs><clipPath id="c"><circle cx="5" cy="5" r="3.6"/></clipPath>'
         '<mask id="m"><rect width="20" height="10" fill="#fff"/><circle cx="15" cy="5" r="3" fill="#000"/></mask></defs>'
         '<rect width="10" height="10" fill="#c60" clip-path="url(#c)"/><rect x="10" width="10" height="10" fill="#06c" mask="url(#m)"/></svg>', 40, 20),
        ("paint: filters", f'<svg {ns} viewBox="0 0 20 10"><defs><filter id="f" x="-50%" y="-50%" width="200%" height="200%">'
         '<feGaussianBlur in="SourceAlpha" stdDeviation="0.8"/><feOffset dx="0.5" dy="0.7" result="o"/>'
         '<feFlood flood-color="#000" flood-opacity="0.5"/><feComposite in2="o" operator="in"/>'
         '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
         '<filter id="b"><feGaussianBlur stdDeviation="0.5"/></filter></defs>'
         '<rect x="2" y="2" width="6" height="6" fill="#fc3" filter="url(#f)"/><circle cx="15" cy="5" r="3" fill="#3cf" filter="url(#b)"/></svg>', 40, 20),
        ("paint: use of a def", f'<svg {ns} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 20 10"><defs><path id="p" d="M0 0 L3 0 L1.5 2.6 Z"/></defs>'
         '<use href="#p" x="2" y="1" fill="#c22"/><use xlink:href="#p" x="8" y="3" fill="#2c2"/>'
         '<use href="#p" transform="translate(14 1) scale(1.5)" fill="#22c"/></svg>', 40, 20),
        ("paint: a group scaled by a half", f'<svg {ns} viewBox="0 0 10 10"><g transform="scale(0.5)">'
         '<path d="M2 2 L18 4 L14 17 Z" fill="#c33"/><circle cx="6.6" cy="14.2" r="3.3" fill="#36c"/></g></svg>', 40, 40),
        ("paint: switch takes the first child that speaks English", f'<svg {ns} viewBox="0 0 20 10"><switch>'
         '<rect systemLanguage="de" width="20" height="10" fill="#c33"/><rect systemLanguage="fr,en" width="20" height="10" fill="#3c3"/>'
         '<rect width="20" height="10" fill="#33c"/></switch></svg>', 40, 20),
        ("paint: nothing drawn", f'<svg {ns} viewBox="0 0 20 10"/>', 40, 20),
        # shape-rendering: the option is only the default for an element that does not say
        ("shape-rendering: the option is the default, an attribute wins", f'<svg {ns} viewBox="0 0 40 10">{shapes}</svg>', 160, 40),
        ("shape-rendering: a group's attribute is inherited", f'<svg {ns} viewBox="0 0 20 10"><g shape-rendering="geometricPrecision">'
         '<circle cx="5.3" cy="5.3" r="4.1" fill="#d22"/></g><circle cx="15.3" cy="5.3" r="4.1" fill="none" stroke="#22d" stroke-width="1.3"/></svg>', 80, 40),
        # text: no fonts are loaded, so the glyphs are absent, which is what skip_system_fonts gives
        ("text: no glyphs without fonts", f'<svg {ns} viewBox="0 0 20 10"><rect width="20" height="10" fill="#eef"/>'
         '<text x="1" y="8" font-family="sans-serif" font-size="6" fill="#000">Hi</text></svg>', 40, 20),
        # refusals
        ("error: empty", "", 40, 20),
        ("error: not an svg", "not svg", 40, 20),
        ("error: truncated", "<svg", 40, 20),
        ("error: another root", "<html/>", 40, 20),
        ("error: zero width", f'<svg {ns} width="0" height="5"/>', 40, 20),
        ("error: negative width", f'<svg {ns} width="-5" height="5"/>', 40, 20),
        ("error: zero target width", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 0, 20),
        ("error: zero target height", f'<svg {ns} viewBox="0 0 20 10">{scene}</svg>', 40, 0),
    ]

    def outcome(svg: str, width: int, height: int, crisp: bool) -> dict:
        kw = {"shape_rendering": "crisp_edges"} if crisp else {}
        try:
            png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height, skip_system_fonts=True, **kw))
        except ValueError as exc:
            try:
                render(svg, width, height, crisp=crisp)
            except ValueError:
                return {"error": str(exc)}
            raise AssertionError("quality.render accepts what svg_to_bytes refuses")
        raw = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
        out = render(svg, width, height, crisp=crisp)
        res = {"width": raw.shape[1], "height": raw.shape[0], "png": png.hex()}
        if raw.shape == out.shape:
            assert np.array_equal(raw, out)      # quality.render leaves an exact-size render alone
        else:
            res["forced"] = [out.shape[1], out.shape[0]]   # Pillow's resize to exactly the size asked for
        return res

    out = []
    for name, svg, width, height in cases:
        out.append({"name": name, "svg": svg, "width": width, "height": height,
                    "aa": outcome(svg, width, height, False), "crisp": outcome(svg, width, height, True)})
    assert sum("error" in c["aa"] for c in out) == 11 and any("forced" in c["aa"] for c in out)
    assert any(c["aa"].get("width") != c["aa"].get("height") for c in out)
    write("render_cases", out)

    # When resvg's size is not the size asked for, `quality.render` resizes with Pillow (nearest
    # for crisp, Lanczos otherwise): a box of another aspect, and, less obviously, a box of
    # the SVG's own aspect once `IntSize::scale_to`'s f32 arithmetic (`ceil(th * W / H)`)
    # rounds, which needs th * W above 2**24 and is not rare at the sizes the intake admits.
    # Small expected images travel as PNGs; the large ones as SHA-256 of the RGBA bytes.
    import hashlib

    def png_of(arr) -> str:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG")
        return buf.getvalue().hex()

    def scene(w: float, h: float) -> str:
        m = min(w, h)
        return ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f40"/>'
                '<stop offset="1" stop-color="#04f" stop-opacity="0.6"/></linearGradient></defs>'
                f'<rect x="{w * .1:.2f}" y="{h * .1:.2f}" width="{w * .5:.2f}" height="{h * .45:.2f}" fill="url(#g)"/>'
                f'<circle cx="{w * .62:.2f}" cy="{h * .6:.2f}" r="{m * .27:.2f}" fill="#2a2" fill-opacity="0.7"/>'
                f'<path d="M{w * .05:.2f} {h * .95:.2f} L{w * .5:.2f} {h * .62:.2f} L{w * .95:.2f} {h * .93:.2f}" fill="none" '
                f'stroke="#000" stroke-width="{m * .03:.2f}" stroke-linejoin="round"/>')

    def fit_of(svg: str, width: int, height: int, crisp: bool) -> list:
        kw = {"shape_rendering": "crisp_edges"} if crisp else {}
        png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height, skip_system_fonts=True, **kw))
        return list(Image.open(io.BytesIO(png)).size)

    small, large = [], []
    tiny_w, tiny_h = 986975, 671143   # 25 x 17 at 1/39479: th * W = 16777575 > 2**24
    for name, svg, width, height in (
        ("aspect: a square stretched into a box twice as wide", f'<svg {ns} viewBox="0 0 10 10">{scene(10, 10)}</svg>', 40, 20),
        ("aspect: a square stretched into a taller box", f'<svg {ns} viewBox="0 0 10 10">{scene(10, 10)}</svg>', 20, 40),
        ("aspect: a wide SVG squeezed into a narrow tall box", f'<svg {ns} viewBox="0 0 20 10">{scene(20, 10)}</svg>', 7, 33),
        ("aspect: a very wide box round a square SVG", f'<svg {ns} viewBox="0 0 10 10">{scene(10, 10)}</svg>', 100000, 1),
        ("aspect: one pixel up to many", f'<svg {ns} viewBox="0 0 1 1"><rect width="1" height="1" fill="#36c" fill-opacity="0.5"/></svg>', 9, 5),
        ("round: the far side rounds up", f'<svg {ns} viewBox="0 0 10 3">{scene(10, 3)}</svg>', 30, 10),
        ("round: fractional 7.4 x 3.6", f'<svg {ns} width="7.4" height="3.6">{scene(7.4, 3.6)}</svg>', 37, 18),
        ("round: tall and thin, taller than 100x, getting shorter (Pillow resizes the height first)",
         f'<svg {ns} width="1.4" height="300.6">{scene(1.4, 300.6)}</svg>', 1, 300),
        ("f32: th * W over 2**24 rounds the fit a row too tall (25 x 17)",
         f'<svg {ns} viewBox="0 0 {tiny_w} {tiny_h}">{scene(tiny_w, tiny_h)}</svg>', 25, 17),
        ("f32: the same for 17 x 25", f'<svg {ns} viewBox="0 0 {tiny_h} {tiny_w}">{scene(tiny_h, tiny_w)}</svg>', 17, 25),
    ):
        row = {"name": name, "svg": svg, "width": width, "height": height}
        for mode, crisp in (("aa", False), ("crisp", True)):
            fit = fit_of(svg, width, height, crisp)
            row[mode] = {"fit": fit, "png": png_of(render(svg, width, height, crisp=crisp))}
        small.append(row)
    assert sum(r["aa"]["fit"] != [r["width"], r["height"]] for r in small) == len(small)

    # Sizes the scorecard can ask for: a viewBox the size of an upload, drawn at its own size.
    # th * W has to pass 2**24 and then not be representable in f32: the smallest upload that
    # rounds wrong at 1x is 16.8 MP (these two), at 3x 5.6 MP (3 * H * W is odd often enough),
    # and 2x and 4x, whose products are multiples of 2 and 4, are exact as long as 1x is.
    for width, height in ((3919, 4281), (4281, 3919)):
        svg = f'<svg {ns} viewBox="0 0 {width} {height}">{scene(width, height)}</svg>'
        row = {"name": f"a {width}x{height} viewBox drawn at its own size", "svg": svg, "width": width, "height": height}
        for mode, crisp in (("aa", False), ("crisp", True)):
            out = render(svg, width, height, crisp=crisp)
            assert out.shape == (height, width, 4)
            row[mode] = {"fit": fit_of(svg, width, height, crisp), "sha256": hashlib.sha256(out.tobytes()).hexdigest()}
        assert row["aa"]["fit"] != [width, height] and row["crisp"]["fit"] != [width, height]
        large.append(row)
    write("render_resize", {"small": small, "large": large})


@exporter("resample")
def _resample() -> None:
    import hashlib

    import numpy as np
    from PIL import Image

    # Pillow's Image.resize on RGBA8, which core/src/resample.rs replicates. The images are
    # not stored: both sides make them from a seed with splitmix64 (little-endian bytes in
    # row-major RGBA order), and only SHA-256 of Pillow's answer travels.
    def splitmix_bytes(seed: int, n: int) -> np.ndarray:
        count = (n + 7) // 8
        with np.errstate(over="ignore"):
            z = np.uint64(seed) + np.arange(1, count + 1, dtype=np.uint64) * np.uint64(0x9E3779B97F4A7C15)
            z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
            z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
            z = z ^ (z >> np.uint64(31))
        return z.astype("<u8").view(np.uint8)[:n]

    def image(seed: int, w: int, h: int, kind: str) -> np.ndarray:
        a = splitmix_bytes(seed, w * h * 4).reshape(h, w, 4).copy()
        alpha = a[..., 3].copy()
        if kind == "mixed":      # a quarter clear, a quarter opaque, the rest as drawn
            a[..., 3] = np.where(alpha % 4 == 0, 0, np.where(alpha % 4 == 1, 255, alpha))
        elif kind == "opaque":
            a[..., 3] = 255
        elif kind == "clear":    # colour with no alpha: premultiplication zeroes it, Lanczos rings
            a[..., 3] = 0
        else:
            raise ValueError(kind)
        return a

    filters = {"nearest": Image.Resampling.NEAREST, "lanczos": Image.Resampling.LANCZOS}
    rng = np.random.default_rng(5)
    shapes = [
        # the off-by-one a render meets, and the other way
        (37, 23, 36, 23), (23, 37, 23, 40), (64, 64, 17, 33), (10, 10, 10, 11), (10, 10, 11, 10), (40, 30, 41, 31),
        (40, 30, 39, 29), (97, 61, 97, 60), (61, 97, 62, 97),
        # one axis alone, both, up and down, to and from a single row, column or pixel
        (10, 10, 40, 10), (10, 10, 10, 40), (10, 10, 5, 10), (10, 10, 10, 5), (20, 10, 7, 33), (7, 33, 20, 10),
        (1, 1, 5, 7), (5, 7, 1, 1), (1, 9, 1, 4), (9, 1, 4, 1), (50, 50, 1, 50), (50, 50, 50, 1), (3, 3, 300, 2),
        # (2 -> 7) puts x on an exact integer, where the running sum of the nearest scale is below it
        (2, 1, 7, 1), (4, 1, 6, 1), (4, 4, 14, 14), (9, 9, 6, 6), (6, 6, 9, 9),
        # more than 100x taller than wide and getting shorter: Pillow resizes the height first
        (3, 401, 3, 5), (2, 250, 2, 100), (1, 101, 1, 50), (3, 301, 7, 299), (3, 301, 2, 7), (2, 202, 2, 201),
        (4, 505, 9, 300), (1, 101, 1, 101), (1, 101, 1, 202),
    ]
    cases = []
    seed = 1000
    for filt in filters:
        for shape in shapes:
            for kind in ("mixed", "opaque") + (("clear",) if shape[0] * shape[1] <= 400 else ()):
                cases.append((seed, *shape, filt, kind))
                seed += 1
        for _ in range(80):
            w, h, ow, oh = (int(v) for v in rng.integers(1, 70, 4))
            cases.append((seed, w, h, ow, oh, filt, "mixed"))
            seed += 1
    out = []
    for seed, w, h, ow, oh, filt, kind in cases:
        got = np.asarray(Image.fromarray(image(seed, w, h, kind), "RGBA").resize((ow, oh), filters[filt]))
        assert got.shape == (oh, ow, 4)
        out.append({"case": f"{seed} {w} {h} {ow} {oh} {filt} {kind}", "sha256": hashlib.sha256(got.tobytes()).hexdigest()})

    # Premultiplication and back, on every (colour, alpha) pair, colours above their alpha too
    # (what a premultiplied Lanczos can leave behind).
    c, a = np.meshgrid(np.arange(256), np.arange(256))
    grid = np.stack([c, (c * 7 + 3) % 256, 255 - c, a], -1).astype(np.uint8)
    pre = np.asarray(Image.fromarray(grid, "RGBA").convert("RGBa"))
    back = np.asarray(Image.fromarray(grid, "RGBa").convert("RGBA"))
    write("resample", {"cases": out, "premultiplied": hashlib.sha256(pre.tobytes()).hexdigest(),
                       "unpremultiplied": hashlib.sha256(back.tobytes()).hexdigest()})


@exporter("drawing")
def _drawing() -> None:
    import hashlib
    import math
    import warnings

    import numpy as np

    from studi0trace.imaging import quality

    # quality.parse and quality.path_polylines, summarised: per contour its point count, flags,
    # paint, coordinate sums and three of its points; per drawing its counters. What the Python
    # raises travels as the exception's class name. JSON has no NaN or infinity: those are strings.
    def num(x):
        x = float(x)
        return x if math.isfinite(x) else repr(x)

    def points(pts: np.ndarray) -> dict:
        n, cols = pts.shape

        def row(i: int):
            return [num(v) for v in pts[i]] if n else None

        return {"n": n, "columns": cols, "sum": [num(pts[:, j].sum()) for j in range(cols)],
                "first": row(0), "mid": row(n // 2), "last": row(-1)}

    def samples(pts: np.ndarray) -> dict:
        # Every point of a contour of up to 32, else 24 spread along it with both ends, as
        # "index x y" triples of repr floats in one string (exact, one line a contour); and a
        # digest of all of them, float64 little-endian with NaN made canonical, which sees an
        # ulp anywhere.
        n = len(pts)
        at = range(n) if n <= 32 else sorted({0, n - 1, *np.linspace(0, n - 1, 24).round().astype(int).tolist()})
        exact = np.where(np.isnan(pts), np.nan, pts).astype("<f8")
        return {"samples": " ".join(f"{i} {float(pts[i, 0])!r} {float(pts[i, 1])!r}" for i in at),
                "digest": hashlib.sha256(exact.tobytes()).hexdigest()[:16]}

    def run(fn, *args):
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore")
            try:
                return fn(*args), None
            except Exception as e:  # ParseError, ValueError, IndexError, OverflowError, RecursionError
                return None, type(e).__name__

    def drawing(svg: str, size) -> dict:
        d, err = run(quality.parse, svg, None if size is None else tuple(size))
        if err:
            return {"error": err}
        return {"elements": d.elements, "segments": d.segments, "strokes": d.strokes, "covers": list(d.covers),
                "contours": [{"element": c.element, "closed": bool(c.closed), "paint": c.paint, "fill_rule": c.fill_rule,
                              "stroke": None if c.stroke is None else num(c.stroke), **points(c.pts),
                              **samples(c.pts)}
                             for c in d.contours]}

    def polylines(d: str) -> dict:
        out, err = run(quality.path_polylines, d)
        if err:
            return {"d": d, "error": err}
        return {"d": d, "subpaths": [{"closed": bool(closed), **points(p)} for p, closed in out]}

    # Traced output (at its own size and at the scorecard's 2x) and a spread of vector truths:
    # arcs and curves, rects with rx, circles, ellipses, polygons, lines, strokes, rotate and
    # matrix transforms, <use>, clipPaths, a DOCTYPE with entities and a latin-1 declaration.
    sizes = {c["stem"]: (c["width"], c["height"]) for c in json.loads((OUT / "render.json").read_text())}
    heldout = ["fluent-color/black-nib.svg", "fluent-color/cityscape-at-dusk.svg",
               "fluent-color/man-in-motorized-wheelchair-facing-right.svg", "fluent-color/smiling-face-with-heart-eyes.svg",
               "fluent-flat/mobile-phone.svg", "noto/u0030.svg", "noto/u1f307.svg", "noto/u1f469-200d-1f33e.svg",
               "noto/u1f97e.svg", "noto/u2640.svg"]
    files = []
    # and a trace small enough to be upsampled, drawn back inside <g transform="scale(0.5)">
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload

    small = load_upload((ROOT / "backend/bench/corpus/synthetic/flat/overlap-128.png").read_bytes(),
                        max_bytes=1 << 30, max_pixels=1 << 30)
    upsampled = VexelEngine().trace(small, VexelParams()).svg
    assert '<g transform="scale(0.5)">' in upsampled
    (OUT / "drawing_overlap-128.svg").write_text(upsampled, encoding="utf-8")
    sizes["overlap-128"] = (small.width, small.height)
    for path in (sorted(OUT.glob("render_*.svg")) + [OUT / "drawing_overlap-128.svg"]
                 + [ROOT / "backend/bench/heldout" / h for h in sorted(heldout)]):
        svg = path.read_text(encoding="utf-8")
        stem = path.stem.removeprefix("render_").removeprefix("drawing_")
        for size in [None] + ([list(sizes[stem])] if stem in sizes else []):
            files.append({"file": path.relative_to(ROOT).as_posix(), "size": size, "drawing": drawing(svg, size)})

    ns = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'

    def svg(body: str, root: str = 'viewBox="0 0 64 64"') -> str:
        return f"<svg {ns} {root}>{body}</svg>"

    tri = 'd="M0 0L4 0 4 4Z"'
    boxed = '<path d="M10 20L60 20 60 45Z" stroke="#000" stroke-width="2"/>'
    cases = [
        # the path grammar
        ("path: every command, absolute",
         svg('<path d="M2 3 L10 4 H20 V12 C22 14 26 18 30 12 S38 6 40 14 Q44 20 48 14 T56 14 A6 4 20 0 1 60 24 Z"/>'), None),
        ("path: every command, relative",
         svg('<path d="m2 3 l8 1 h10 v8 c2 2 6 6 10 0 s8 -6 10 2 q4 6 8 0 t8 0 a6 4 20 0 1 4 10 z"/>'), None),
        ("path: arguments repeated without the letter",
         svg('<path d="M1 1 2 2 3 1 L4 4 5 5 H6 7 V8 9 C1 1 2 2 3 3 4 4 5 5 6 6 S7 7 8 8 9 9 10 10 Q1 2 3 4 5 6 7 8'
             ' T9 9 10 10 A1 1 0 0 1 3 3 2 2 0 1 0 6 6"/><path d="m1 1 2 2 3 1 l1 1 1 1 h1 1 v1 1 c1 1 2 2 3 3 4 4 5 5 6 6'
             ' s1 1 2 2 3 3 4 4 q1 2 3 4 5 6 7 8 t1 1 2 2 a1 1 0 0 1 3 3 2 2 0 1 0 6 6"/>'), None),
        ("path: closepath, then drawing on without a moveto",
         svg('<path d="M0 0 L10 0 10 10 Z L20 20 30 20 Z l5 5 5 -5 z m3 3 h4 v4 z z"/>'), None),
        ("path: number forms and separators",
         svg('<path d="M.5.5L-1-2e1 3E-1,4 1.5.5+5+6 1e2e3 4 7. 8.Z"/>'), None),
        ("path: S and T reflect only after their own kind",
         svg('<path d="M0 0 Q5 5 10 0 S15 5 20 0 C22 2 24 2 26 0 T30 0 T34 0 S38 4 40 0 L42 0 S44 4 46 0 T50 0'
             ' C51 1 52 1 53 0 C1 2 S56 4 58 0 Q1 2 T60 1"/>'), None),
        ("path: arcs",  # both flags each way, rx 0, radii scaled up, rotated, zero length, negative radii, run-together flags
         svg('<path d="M10 10 A5 5 0 0 0 20 10 A5 5 0 0 1 30 10 A5 5 0 1 0 40 10 A5 5 0 1 1 50 10 A0 5 0 0 1 55 15'
             ' A1 1 0 0 1 60 30 A3 6 45 1 1 40 40 A2 2 0 0 1 40 40 A-4 -3 0 0 1 30 45 a1 1 0 00.5.5 A2 2 0 2 -1 20 50'
             ' A4 4 90 0 1 10 50.000001 a3 2 -30 1 0 -5 -5 A1 1 0 0 1 -5 35.00000001"/>'), None),
        ("path: rotated arcs whose radii are scaled to reach",  # co = sqrt(num/den) magnifies any rounding
         svg('<path d="M4.438 41 A8 29.4 27 0 0 61 -3.089"/><path d="M10 10 A3 7 33 1 1 50 30"/>'
             '<path d="M0 60 a2 5 -60 0 1 30 -20" fill="none" stroke="#000"/><path d="M5 5 A1 1 45 1 0 40 45"/>'
             '<path d="M60 2 A4 9 115 0 0 3 58 A0.5 20 -170 1 1 60 2Z"/><path d="M1 1 A30 2 89.5 0 1 63 63"/>'
             '<path transform="rotate(15 32 32)" d="M8 32 A6 3 71 1 0 56 30"/>'), None),
        ("path: truncated, empty and missing data",
         svg('<path d="M0 0 L5"/><path d="M1 2 3"/><path d="Q1 2"/><path d=""/><path/><path d="5 5 L1 1 2 2"/>'
             '<path d="M5 Z L1 2 3 4"/><path d="M5 Z V3 4"/><path d="M5 Z m1 2 L3 3"/><path d="M Z A0 1 0 0 1 9 9 L3 3"/>'
             '<path d="M0 0 C1 2 3"/><path d="M0 0 A1 1 0 0"/><path d="M5 Z C1 2 3 4 5 6 S7 8 9 9 T1 1"/>'), None),
        ("path: infinite coordinates",
         svg('<path d="M1e999 0 L5 5"/><path d="M0 0 L1e999 0 Z" fill="none" stroke="#000"/>'
             '<path d="M1e999 m-1e999 z" fill="none"/>'), None),
        ("path: unicode digits", svg('<path d="M ٣ ٤ L ５ ９ L 1٠ 0 Z"/>'), None),
        # transforms
        ("transforms", svg("".join(f'<path transform="{t}" d="M1 2 L5 2 L5 6 Z"/>' for t in [
            "translate(1 2)", "translate(3)", "translate(1,2)", "scale(2)", "scale(2 3)", "scale(-1, 1)", "rotate(30)",
            "rotate(30 10 10)", "rotate(30 5)", "rotate(-45,4,4)", "skewX(20)", "skewY(20)", "matrix(1 0.2 0.3 1 5 6)",
            "matrix(1,2,3)", "translate(5,5) rotate(10) scale(1.5)", "translate(5 5)junk", "translate(5 5", "foo(1) translate(2 0)",
            "Translate(5)", "translate (3 4)", "  scale(0.5)  translate(4)", "rotate(90 1e999 0)", "scale(1e999)",
            "translate(1e999 0)", "matrix(1 0 0 1 1e999 0) scale(2)", "rotate(1e308)", ""])
            + '<g transform="translate(10 0)"><g transform="scale(2)"><path transform="rotate(5)" d="M0 0L3 0L3 3Z"'
              ' stroke="#f00" stroke-width="2"/></g></g>'), None),
        # <use>, <defs>, what is skipped and what is not drawn
        ("use", svg('<defs><path id="p" d="M0 0L4 0L4 4Z" transform="translate(1 1)" fill="#f00" opacity="0.8"/>'
                    '<g id="grp" fill="#0f0"><path id="inner" d="M0 0L1 1 1 0Z"/></g><circle id="c" r="2"/></defs>'
                    '<use href="#p" x="10" y="5"/><use xlink:href="#p" x="20" transform="rotate(10)" fill="#00f" opacity="0.5"/>'
                    '<use href="#grp"/><use href="#inner" y="30"/><use href="#missing"/><use href="" xlink:href="#p" y="40"/>'
                    '<use href="##p" x="50"/><use href="p" x="1e999" y="3"/>'
                    '<use xlink:href="#c" x="3" y="3" stroke="#000" stroke-width="3" transform="scale(2)"/>'
                    '<path id="shown" d="M40 40 L50 40 L50 50Z"/><use href="#shown" x="-30"/><use id="self" href="#self"/>'), None),
        ("skipped and unknown elements",
         svg('<defs><path d="M0 0L9 9 9 0Z"/></defs><linearGradient><path d="M0 0L9 9 9 0Z"/></linearGradient>'
             '<radialGradient/><filter><path d="M0 0L1 1"/></filter><mask><path d="M0 0L1 1"/></mask>'
             '<clipPath><path d="M0 0L1 1"/></clipPath><symbol><path d="M0 0L1 1"/></symbol><pattern><path d="M0 0L1 1"/></pattern>'
             '<style>path{fill:red}</style><title>t</title><desc>d</desc><metadata><path d="M0 0L1 1"/></metadata>'
             '<a><path d="M0 0L1 1 1 0Z"/></a><switch><path d="M0 0L1 1 1 0Z"/></switch><text x="1" y="1">hi</text>'
             '<image href="x.png" width="4" height="4"/><foo:path xmlns:foo="urn:foo" d="M0 0L5 5 5 0Z"/>'
             '<svg x="10" y="10" transform="scale(3)" opacity="0.1" fill="#123"><path d="M0 0L2 0 2 2Z"/></svg>'
             '<path class="c" d="M3 3L6 3 6 6Z" display="none" visibility="hidden" style="display:none"/>'), None),
        ("no error: bad transforms that are never read",
         svg('<defs><path transform="scale()" d="M0 0L1 1"/></defs><svg transform="translate()"><path d="M0 0L1 1 1 0Z"/></svg>'
             '<use href="#nothing" transform="rotate()"/>'), None),
        # paint
        ("paint", svg(
            f'<path {tri} fill="none" stroke="#f00" stroke-width="2"/><path {tri} fill="none" stroke="none"/>'
            f'<path {tri} fill="" stroke=""/><path {tri} fill="None"/>'
            f'<path {tri} fill="#f00" style="fill:#0f0;stroke: #00f ;stroke-width:3px"/>'
            f'<path {tri} style=" fill : #abc ; fill-opacity:0.2;opacity:0.9"/><path {tri} style="xfill:#f00;fill-opacity:0.5"/>'
            f'<g fill="#f0f" stroke="#0ff" stroke-width="0.5" fill-rule="evenodd" opacity="0.5"><path {tri} opacity="0.9"/>'
            f'<path {tri} style="fill-rule: evenodd "/><g style="stroke:none" fill-rule="nonzero"><path {tri}/></g></g>'
            f'<path {tri} fill-rule=" evenodd"/><path {tri} fill-rule="EvenOdd"/><path {tri} fill-opacity="50%"/>'
            f'<path {tri} fill-opacity="49%"/><path {tri} fill="none" stroke="#000" stroke-opacity="0.4"/>'
            f'<path {tri} fill="none" stroke="#000" stroke-opacity="0.4" opacity="2"/><path {tri} opacity="50%" fill-opacity="abc"/>'
            f'<path {tri} stroke="#000" stroke-width="abc"/><path {tri} stroke="#000" stroke-width="50%" transform="scale(2 3)"/>'
            f'<path {tri} stroke="#000" stroke-width="1" transform="matrix(0 1 1 0 0 0)"/>'
            f'<path {tri} stroke="#000" transform="scale(0)"/><path {tri} stroke="#000" transform="rotate(33) scale(1.7 0.3)"/>'
            '<line x1="1" y1="2" x2="30" y2="40" stroke="#000" stroke-width="4"/><line x1="1" y1="2" x2="3" y2="4" fill="#f00"/>'
            '<polyline points="1 1 5 5 9 1" fill="#f00" stroke="#00f"/><rect width="5" height="5" class="k"/>'
            f'<style>.k{{fill:none}}</style><path {tri} fill="url(#g)" stroke="currentColor"/>'), None),
        # shapes
        ("shapes", svg(
            '<rect width="10" height="6"/><rect x="1" y="2" width="10" height="6" rx="2"/><rect width="10" height="6" ry="2"/>'
            '<rect width="10" height="6" rx="2" ry="1"/><rect width="10" height="6" rx="9"/><rect width="10" height="6" rx="auto" ry="3"/>'
            '<rect width="10" height="6" rx="0" ry="3"/><rect width="-10" height="6" rx="2"/><rect width="50%" height="6px" rx="1.5"/>'
            '<rect/><rect x="0.1" y="0.2" width="0.3" height="0.7" rx="0.05"/>'
            '<circle cx="5" cy="6" r="3"/><circle r="0"/><circle r="-2"/><circle r="40"/><ellipse cx="3" cy="4" rx="5" ry="2"/>'
            '<ellipse rx="1" ry="9"/><ellipse rx="1e-300" ry="0"/>'
            '<polygon points="1,1 5,1 5,5 7"/><polygon points=""/><polygon points="3 3"/><polygon/>'
            '<polyline points="1 1 5 1 5 5"/><polyline points="2 2"/><line x1="1" y1="2" x2="3" y2="4"/><line/>'), None),
        ("rect whose width is infinite", svg('<rect width="1e999" height="5" rx="1"/>'), None),
        ("rect whose width is infinite, unrounded", svg('<rect width="1e999" height="5"/>'), None),
        # the root, namespaces and the XML itself
        ("root attributes",
         '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" fill="#f00" opacity="0.1" transform="scale(5)"'
         f' stroke="#000"><path {tri}/></svg>', None),
        ("root is a group",
         f'<g xmlns="http://www.w3.org/2000/svg" transform="translate(5 5)" opacity="0.4"><path {tri}/></g>', None),
        ("root is a path", f'<path xmlns="http://www.w3.org/2000/svg" {tri} transform="scale(2)" fill="#0f0"/>', None),
        ("root is defs", f'<defs xmlns="http://www.w3.org/2000/svg"><path {tri}/></defs>', None),
        ("no namespace", f'<svg viewBox="0 0 10 10"><path {tri}/><circle r="2"/></svg>', None),
        ("prefixed SVG namespace",
         '<s:svg xmlns:s="http://www.w3.org/2000/svg" xmlns:l="http://www.w3.org/1999/xlink"><s:defs>'
         f'<s:path id="q" d="M0 0L1 0 1 1Z"/></s:defs><s:use l:href="#q" x="2"/><s:path {tri}/></s:svg>', None),
        ("xlink bound to another URI",
         '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="urn:not-xlink"><defs><path id="q" d="M0 0L1 0 1 1Z"/></defs>'
         '<use xlink:href="#q"/><use href="#q" x="1"/></svg>', None),
        ("doctype, entities, a latin-1 declaration, a comment and a PI",
         '<?xml version="1.0" encoding="iso-8859-1"?>\n<!DOCTYPE svg [<!ENTITY ns "http://www.w3.org/2000/svg">'
         '<!ENTITY d "M0 0L7 0 7 7Z">]>\n<!-- c --><svg xmlns="&ns;"><path d="&d;" fill="&#x23;f00"/><?pi x?></svg>', None),
        ("duplicate ids: the last wins",
         svg('<defs><path id="a" d="M0 0L1 0 1 1Z"/><path id="a" d="M0 0L9 0 9 9Z"/></defs><use href="#a"/>'), None),
        ("attribute whitespace", svg('<path d="M0\n0\tL5\r\n5 5 0Z" style="fill:&#9;#f00&#9;;stroke:\n#000\n"/>'), None),
        ("groups nested 900 deep", svg("<g>" * 900 + '<path d="M0 0L1 1 1 0Z"/>' + "</g>" * 900), None),
        # size: root user units onto a raster through the viewBox
        ("size: viewBox with an offset", svg(boxed, 'viewBox="10 20 100 50"'), [200, 100]),
        ("size: the same without a size", svg(boxed, 'viewBox="10 20 100 50"'), None),
        ("size: commas and a non-uniform scale", svg(boxed, 'viewBox="0,0,10,20"'), [30, 30]),
        ("size: a viewBox of three numbers is ignored", svg(boxed, 'viewBox="0 0 10"'), [30, 30]),
        ("size: a viewBox of five numbers is ignored", svg(boxed, 'viewBox="0 0 10 10 10"'), [30, 30]),
        ("size: a viewBox of zero width is ignored", svg(boxed, 'viewBox="0 0 0 10"'), [30, 30]),
        ("size: no viewBox", svg(boxed, 'width="10" height="10"'), [30, 30]),
        ("size: an infinite viewBox origin", svg(boxed, 'viewBox="-1e999 2 10 10"'), [30, 30]),
        ("size: an infinite viewBox width", svg(boxed, 'viewBox="0 0 1e999 10"'), [30, 30]),
        ("size: zero", svg(boxed, 'viewBox="0 0 10 10"'), [0, 0]),
        # what the Python raises
        ("error: a moveto of one number, then a line", svg('<path d="M5 L1 2"/>'), None),
        ("error: a bare relative moveto", svg('<path d="m"/>'), None),
        ("error: a horizontal line from a one-number point", svg('<path d="M5 Z H3"/>'), None),
        ("error: a relative vertical line from a one-number point", svg('<path d="M5 Z v3 4"/>'), None),
        ("error: an arc from a one-number point", svg('<path d="M5 Z A1 1 0 0 1 9 9"/>'), None),
        ("error: an arc from an empty point", svg('<path d="M Z A1 1 0 0 1 9 9"/>'), None),
        ("error: closing back to a one-number start", svg('<path d="M5 Z z L3 3 z"/>'), None),
        ("error: a curve from an empty point", svg('<path d="M Z Z C1 2 3 4 5 6"/>'), None),
        ("error: an arc of infinite radius", svg('<path d="M0 0 A1e999 1 0 0 1 5 5"/>'), None),
        ("error: an arc whose radii square to zero", svg('<path d="M0 0 A1e-200 1e-200 0 0 1 3 3"/>'), None),
        ("error: an arc rotated by infinity", svg('<path d="M0 0 A1 1 1e999 0 1 5 5"/>'), None),
        ("error: a cubic through infinity", svg('<path d="M0 0 C1e999 0 1 1 2 2"/>'), None),
        ("error: a circle of infinite radius", svg('<circle r="1e999"/>'), None),
        ("error: a one-column subpath painted", svg('<path d="M1e999 m-1e999 z"/>'), None),
        ("error: translate without numbers", svg('<g transform="translate()"><path d="M0 0L1 1"/></g>'), None),
        ("error: scale without numbers", svg('<path transform="scale( )" d="M0 0L1 1"/>'), None),
        ("error: rotate by infinity", svg('<path transform="rotate(1e999)" d="M0 0L1 1"/>'), None),
        ("error: a bad transform on an element that is not drawn", svg('<text transform="translate()">x</text>'), None),
        ("error: a bad transform on a use", svg('<defs><path id="p" d="M0 0L1 1"/></defs><use href="#p" transform="rotate()"/>'), None),
        ("error: malformed XML", "<svg><path></svg>", None),
        ("error: an empty document", "", None),
        ("error: an undefined entity", svg('<path d="&nope;"/>'), None),
        ("error: an unbound prefix", '<svg><x:path d="M0 0L1 1"/></svg>', None),
        ("error: groups nested 1000 deep", svg("<g>" * 1000 + '<path d="M0 0L1 1 1 0Z"/>' + "</g>" * 1000), None),
    ]
    cases = [{"name": name, "svg": text, "size": size, "drawing": drawing(text, size)} for name, text, size in cases]

    # path_polylines on its own: what it makes of degenerate data, and of every prefix of one
    # path, so that each place the data can be cut short is held to the Python.
    paths = [polylines(d) for d in [
        "M 5 Z L 1 2 3 4", "M 5 L 1 2", "M1e999 m-1e999 z", "M 0 0 L 5", "m", "M Z l 1 2", "M5 Z V 3 4", "M5 Z v 3 4", "M5 Z H 3",
        "M5 Z C 1 2 3 4 5 6", "M5 Z S 1 2 3 4 S 5 6 7 8", "M5 Z T 1 2 T 3 4", "M5 Z A 1 1 0 0 1 5 5", "M5 Z A 1 1 0 0 1 9 9",
        "M5 Z a 0 1 0 0 1 9 9 L 3 3", "M Z A 0 1 0 0 1 9 9 L 3 3", "M Z A 1 1 0 0 1 9 9", "M 5 m", "M 5 m 3", "M Z m 3",
        "M5 Z z L 3 3 z", "M 5 Z m 1 2 L 3 3", "M1e999 0 L 5 5", "M0 0 A 1e999 1 0 0 1 5 5", "M0 0 A 1 1 1e999 0 1 5 5",
        "M0 0 C 1e999 0 1 1 2 2", "M0 0L1e999 0 Z", "M 1 2 3", "M1 2 3 4 5", "Q1 2", "M0 0 Q 1 1 2 0 T 4 0 5 5 T", "5 5 L 1 1 2 2",
        "M0 0 L1 1ZL 3 3", "M0,0 1,1 2,0z m 5 5 l 1 1", "M.5.5.5.5", "M1e2e3 4", "M0 0a1 1 0 00.5.5", "M0 0a1 1 0 1 0 5 5",
        "M0 0 A 1 2 30 1 1 10 0", "M1e308 0 L-1e308 0 m1e308 1e308 l1e308 1e308", "M0 0 C 1e300 0 -1e300 0 1 1",
        "M-0 -0 L0 0 Z", "M0 0 A5 5 0 0 1 0.00001 0 A5 5 0 0 1 10 10 A 5 5 360 1 1 0 0 A 5 5 -720 0 0 1 1",
        "M 5 Z v 3", "M 5 Z m 1 v 3 h 2", "M 5 Z Q 1 2 3 4 T 5 6 T 7 8", "M 5 Z T 1 2 T 3 4 S 5 6 7 8", "",
    ]]
    long = ("M10 20 L30-5.5e1 h4v-3 H12 V8 C1 2 3 4 5 6 s7 8 9 10 Q11 12 13 14 t15 16 A5 3 20 1 0 40 40"
            " a2 2 0 0 1 -3 -3 Z m1 1 l2 2 z")
    prefixes = [polylines(long[:k]) for k in range(len(long) + 1)]
    write("drawing", {"files": files, "cases": cases, "paths": paths, "prefixes": prefixes})


@exporter("holes")
def _holes() -> None:
    import hashlib
    import re

    import numpy as np
    from PIL import Image
    from scipy import ndimage

    from studi0trace.imaging import quality

    # quality.holes and the opaque mask of quality.Reference. A trace of a corpus item is usually
    # clean, and zero holes agreeing with zero holes proves nothing, so most of the cases are made:
    # tiny sources and SVGs that leave real holes, of every kind the count tells apart, at scales
    # 1 to 4. Only the alpha of a source is read; its colour is random so that a port reading the
    # wrong channel fails.
    rng = np.random.default_rng(10)
    ns = 'xmlns="http://www.w3.org/2000/svg"'

    def pack(mask: np.ndarray) -> str:
        return "".join("1" if v else "0" for v in mask.ravel())

    def opaque_of(rgba: np.ndarray) -> np.ndarray:
        # quality.Reference.__init__'s two lines: a Reference of a source under 8 px a side cannot
        # be built (its Canny has no room), so those take the expression itself.
        if min(rgba.shape[:2]) >= 8:
            return quality.Reference(rgba).opaque
        a_src = rgba[..., 3].astype(np.float32) / 255.0
        return ndimage.binary_erosion(a_src >= 0.99, structure=np.ones((3, 3), bool), border_value=0)

    def plain(card: dict) -> dict:
        return {"hole_subpx": int(card["hole_subpx"]), "hole_px": float(card["hole_px"]),
                "hole_clusters": int(card["hole_clusters"]), "pinholes": int(card["pinholes"]),
                "_clusters": [{"x": float(c["x"]), "y": float(c["y"]), "subpx": int(c["subpx"]),
                               "min_cover": float(c["min_cover"]), "deficit_px": float(c["deficit_px"]),
                               "pinhole": bool(c["pinhole"])} for c in card["_clusters"]]}

    def svg(w: int, h: int, body: str) -> str:
        return f'<svg {ns} viewBox="0 0 {w} {h}">{body}</svg>'

    def box(x, y, w, h) -> str:
        return f"M{x} {y}h{w}v{h}h-{w}z"

    def ball(cx, cy, r) -> str:
        return f"M{cx - r} {cy}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0z"

    def with_holes(w: int, h: int, holes: list[str], fill: str = "#345") -> str:
        """A slab over the whole canvas with `holes` (subpaths) cut out of it."""
        return f'<path fill-rule="evenodd" fill="{fill}" d="{box(0, 0, w, h)}{"".join(holes)}"/>'

    def paint(shape: str, opacity: float) -> str:
        return f'<path d="{shape}" fill="#a31" fill-opacity="{opacity}"/>'

    def pitted(w: int, h: int, fill: str) -> str:
        """The background rect of a trace, cut through in 80 places by holes of 0.3 to 6 px, some painted over at 0.3 to 0.93."""
        holes, overlays = [], []
        for i in range(80):
            size = rng.uniform(0.3, 6.0, 2).round(2)
            x, y = (rng.uniform(0, 1, 2) * (np.array([w, h]) - size)).round(2)
            holes.append(box(x, y, size[0], size[1]) if i % 3 else ball(x + size[0] / 2, y + size[0] / 2, size[0] / 2))
            if i % 3 == 1:
                overlays.append(paint(holes[-1], round(float(rng.uniform(0.3, 0.93)), 2)))
        return with_holes(w, h, holes, fill) + "".join(overlays)

    # -- the corpus items, traced; and the same traces with the background left out or pitted, which opens real holes
    traced = []
    for rel in ("real/logo/vexel-wordmark-512.png", "synthetic/shadow/glow-128.png"):
        stem = Path(rel).stem
        text = (OUT / f"render_{stem}.svg").read_text(encoding="utf-8")
        rgba = np.asarray(Image.open(ROOT / "backend/bench/corpus" / rel).convert("RGBA"))
        opaque = opaque_of(rgba)
        shapes = list(re.finditer(r"<(?:rect|path|circle)\b[^>]*/>", text))
        bg = shapes[0]  # the background rect, drawn first: everything else is on top of it
        w, h = rgba.shape[1], rgba.shape[0]
        fill = re.search(r'fill="([^"]*)"', bg.group(0)).group(1)
        variants = [("trace", text),
                    ("without the background", text[:bg.start()] + text[bg.end():]),
                    ("pitted background", text[:bg.start()] + pitted(w, h, fill) + text[bg.end():])]
        for name, variant in variants:
            for scale in (4, 2):
                card = plain(quality.holes(variant, rgba, scale))
                traced.append({"stem": stem, "source": f"bench/corpus/{rel}", "variant": name, "svg": variant,
                               "scale": scale, "opaque_count": int(opaque.sum()),
                               "opaque_sha256": hashlib.sha256(opaque.astype(np.uint8).tobytes()).hexdigest(),
                               "card": card})

    # -- made sources (RGBA, random colour; hex)
    def make(alpha: np.ndarray) -> np.ndarray:
        rgba = rng.integers(0, 256, (*alpha.shape, 4), dtype=np.uint8)
        rgba[..., 3] = alpha
        return rgba

    yy, xx = np.mgrid[0:24, 0:32]
    rr = np.hypot(xx + 0.5 - 16, yy + 0.5 - 12)
    disc = np.where(rr < 10, 255, np.where(rr < 11.5, 128, 0)).astype(np.uint8)
    slab = np.full((18, 24), 255, np.uint8)
    slab[:, 6:8] = 252       # just under the opaque threshold, and so is what its neighbours lose to erosion
    slab[11:13, :] = 253     # just over it
    slab[2:4, 14:18] = 128
    sources = {
        "solid": make(np.full((14, 20), 255, np.uint8)),
        "solid6": make(np.full((6, 6), 255, np.uint8)),
        "disc": make(disc),
        "slab": make(slab),
        "clear": make(np.zeros((10, 16), np.uint8)),
        "dot1": make(np.full((1, 1), 255, np.uint8)),
        "dot2": make(np.full((2, 2), 255, np.uint8)),
        "dot3": make(np.full((3, 3), 255, np.uint8)),
        "no_rows": np.zeros((0, 5, 4), np.uint8),
        "no_columns": np.zeros((4, 0, 4), np.uint8),
    }
    source_cards = {k: {"w": int(v.shape[1]), "h": int(v.shape[0]), "rgba": v.tobytes().hex(), "opaque": pack(opaque_of(v))}
                    for k, v in sources.items()}

    synthetic = []

    def add(name: str, source: str, text: str, scales=(1, 2, 3, 4), opaque: np.ndarray | None = None):
        rgba = sources[source]
        for scale in scales:
            entry = {"name": f"{name} x{scale}", "source": source, "svg": text, "scale": scale,
                     "opaque": None if opaque is None else pack(opaque), "error": None}
            try:
                entry["card"] = plain(quality.holes(text, rgba, scale, opaque))
            except Exception as e:  # what the Python refuses; the port refuses it too
                entry["card"], entry["error"] = None, type(e).__name__
            synthetic.append(entry)

    W, H = 20, 14
    # (a) covered exactly
    add("solid: covered", "solid", svg(W, H, '<rect width="20" height="14" fill="#246"/>'))
    # (b) a strip left uncovered, its edges between sub-pixels
    add("solid: strip", "solid", svg(W, H, '<rect width="9.13" height="14" fill="#246"/><rect x="10.4" width="9.6" height="14" fill="#246"/>'))
    # (c) holes of several sizes, empty and painted over at 0.3 to 0.96 (under 0.5 is a pinhole; over 0.95 is no hole)
    pins = [ball(5.5, 5.5, 0.4), box(9, 3, 2, 2), box(2.3, 9.4, 1.6, 1.2), box(13, 8, 1, 1), box(3, 12, 1.5, 1.0),
            ball(15.5, 3.5, 1.2), box(7, 10, 1, 1), box(16.25, 11.25, 0.5, 0.5)]
    add("solid: pin-holes", "solid", svg(W, H, with_holes(W, H, pins) + paint(box(13, 8, 1, 1), 0.3) + paint(box(3, 12, 1.5, 1.0), 0.7)
                                        + paint(ball(15.5, 3.5, 1.2), 0.9) + paint(box(7, 10, 1, 1), 0.96)))
    # (d) holes that touch at a corner (one cluster when eight-connected), a V of three, and holes at the border
    diag = [box(4, 4, 1, 1), box(5, 5, 1, 1), box(8, 7, 1, 1), box(9, 8, 1, 1), box(10, 7, 1, 1), box(14, 10, 1, 1),
            box(1, 1, 2, 2), box(0, 6, 3, 3), box(17, 11, 3, 3)]
    add("solid: corner-touching and border holes", "solid", svg(W, H, with_holes(W, H, diag)))
    # equal holes: the clusters come back in the order they were found
    ties = [box(3, 3, 1, 1), box(10, 3, 1, 1), box(3, 9, 1, 1), box(10, 9, 1, 1), box(16, 10, 1, 1), box(14, 6, 2, 2), box(6, 6, 0.5, 0.5)]
    add("solid: equal holes", "solid", svg(W, H, with_holes(W, H, ties)))
    # (f) nothing drawn: every opaque sub-pixel is a hole
    add("solid: empty svg", "solid", f'<svg {ns} viewBox="0 0 20 14"/>')
    add("solid: half covered", "solid", svg(W, H, '<rect x="10" width="10" height="14" fill="#246"/>'))
    mine = np.zeros((H, W), bool)  # a mask of its own reaches the border, which the eroded one never does
    mine[0:8, 0:4] = True
    add("solid: a mask of your own", "solid", svg(W, H, with_holes(W, H, pins[:3] + [box(0, 0, 2, 14)])), opaque=mine)
    add("solid: a mask that is all clear", "solid", svg(W, H, with_holes(W, H, pins)), opaque=np.zeros((H, W), bool))
    # an SVG that does not parse and a scale of none: the Python raises, the port refuses
    add("solid: not an svg", "solid", "<svg", scales=(2,))
    add("solid: scale 0", "solid", svg(W, H, '<rect width="20" height="14"/>'), scales=(0,))

    cx, cy = 16, 12
    add("disc: covered", "disc", svg(32, 24, f'<circle cx="{cx}" cy="{cy}" r="12" fill="#246"/>'))
    add("disc: a ring left", "disc", svg(32, 24, f'<circle cx="{cx}" cy="{cy}" r="8.5" fill="#246"/>'))
    add("disc: translucent", "disc", svg(32, 24, f'<circle cx="{cx}" cy="{cy}" r="12" fill="#246" fill-opacity="0.6"/>'))
    add("disc: a hole in the middle", "disc", svg(32, 24, f'<path fill-rule="evenodd" fill="#246" d="{ball(cx, cy, 12)}{ball(cx, cy, 3)}"/>'))
    add("disc: half at 0.4", "disc", svg(32, 24, f'<path d="{box(0, 0, 16, 24)}" fill="#246"/>'
                                                 f'<path d="{box(16, 0, 16, 24)}" fill="#246" fill-opacity="0.4"/>'))
    add("disc: empty", "disc", f'<svg {ns} viewBox="0 0 32 24"/>')

    add("slab: covered", "slab", svg(24, 18, '<rect width="24" height="18" fill="#246"/>'))
    add("slab: right half", "slab", svg(24, 18, '<rect x="12" width="12" height="18" fill="#246"/>'))
    add("slab: empty", "slab", f'<svg {ns} viewBox="0 0 24 18"/>')
    add("clear: anything", "clear", svg(16, 10, '<rect width="8" height="10" fill="#246"/>'))
    add("clear: scale 0", "clear", svg(16, 10, ""), scales=(0,))
    for name in ("dot1", "dot2", "dot3"):
        n = sources[name].shape[0]
        add(f"{name}: empty", name, f'<svg {ns} viewBox="0 0 {n} {n}"/>', scales=(1, 3))
    for name in ("no_rows", "no_columns"):
        add(f"{name}: anything", name, svg(4, 4, '<rect width="4" height="4"/>'), scales=(2,))

    # the thresholds: a whole source painted at one opacity. Hole: alpha < 242.25. Pinhole: cover < 0.5
    for k in (238, 240, 241, 242, 243, 244, 246, 250, 255):
        add(f"opacity {k}/255", "solid6", svg(6, 6, f'<rect width="6" height="6" fill="#246" fill-opacity="{k / 255}"/>'), scales=(1, 3))
    for k in (100, 125, 126, 127, 128, 129, 131, 200):
        add(f"opacity {k}/255", "solid6", svg(6, 6, f'<rect width="6" height="6" fill="#246" fill-opacity="{k / 255}"/>'), scales=(1, 3))

    # -- the opaque mask alone: random sources over the alphas around the threshold, and the threshold itself
    erosion = []
    palette = np.array([0, 1, 128, 252, 253, 254, 255], np.uint8)
    for i in range(28):
        h, w = int(rng.integers(1, 17)), int(rng.integers(1, 21))
        p = np.array([0.04, 0.02, 0.06, 0.08, 0.05, 0.05, 0.70])
        rgba = make(rng.choice(palette, (h, w), p=p))
        erosion.append({"h": h, "w": w, "rgba": rgba.tobytes().hex(), "opaque": pack(opaque_of(rgba))})
    ramp = make(np.tile(np.arange(256, dtype=np.uint8), (5, 1)))  # one column of each alpha: 253 is the first that survives
    erosion.append({"h": 5, "w": 256, "rgba": ramp.tobytes().hex(), "opaque": pack(opaque_of(ramp))})

    # -- ndimage.label with the 3x3 structure: which label each pixel gets
    labels = []
    for i in range(36):
        h, w = int(rng.integers(1, 15)), int(rng.integers(1, 19))
        mask = rng.random((h, w)) < [0.15, 0.3, 0.45, 0.55, 0.65, 0.8][i % 6]
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
        labels.append({"h": h, "w": w, "mask": pack(mask), "n": int(n), "labels": lab.ravel().tolist()})
    shapes = {  # late merges, corner touches and a spiral
        "u": ["1.1", "1.1", "111"],
        "diagonals": ["1....", ".1...", "..1.1", "...1.", "..1.1"],
        "spiral": ["1111111", "......1", "11111.1", "1...1.1", "1.111.1", "1.....1", "1111111"],
        "comb": ["1.1.1.1", "1.1.1.1", "1111111", ".......", "1.1.1.1"],
    }
    for name, rows in shapes.items():
        mask = np.array([[c == "1" for c in row] for row in rows])
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
        labels.append({"name": name, "h": mask.shape[0], "w": mask.shape[1], "mask": pack(mask), "n": int(n), "labels": lab.ravel().tolist()})

    # The golden must not be vacuous: real holes, pinholes, holes that are not pinholes, equal deficits.
    cards = [c["card"] for c in synthetic if c["card"] is not None]
    assert sum(c["hole_clusters"] for c in cards) > 100, "too few holes in the made cases"
    assert sum(c["pinholes"] for c in cards) > 20, "too few pinholes in the made cases"
    assert any(c["hole_clusters"] > c["pinholes"] for c in cards), "no hole that is not a pinhole"
    assert any(c["hole_clusters"] >= 2 and c["hole_subpx"] > c["hole_clusters"] for c in cards)
    assert any(any(a["deficit_px"] == b["deficit_px"] and (a["y"], a["x"]) != (b["y"], b["x"]) for a, b in zip(c["_clusters"], c["_clusters"][1:]))
               for c in cards), "no equal deficits to order"
    assert {c["scale"] for c in synthetic if c["card"] and c["card"]["hole_clusters"]} == {1, 2, 3, 4}
    assert any(c["card"] and c["card"]["hole_clusters"] for c in traced), "no holes in any traced case"
    assert sum(c["error"] is not None for c in synthetic) == 2
    assert any(c["opaque"] is not None and c["card"]["hole_clusters"] for c in synthetic)
    write("holes", {"traced": traced, "sources": source_cards, "synthetic": synthetic, "erosion": erosion, "labels": labels})


@exporter("geometry")
def _geometry() -> None:
    import hashlib
    import math

    import numpy as np

    from studi0trace.engines.presets import fixed_presets
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging import quality as Q
    from studi0trace.imaging.intake import load_upload

    # quality.geometry_card and every helper it calls. Two files: `geometry_helpers.json` holds
    # each helper's answer on made outlines and on outlines of a trace, so that a failure names
    # the helper; `geometry.json` holds whole cards, on traces (every fixed preset), vector
    # truths, the synthetic corpus and made SVGs that set off each counter. Arrays of floats are
    # kept whole when short and as a SHA-256 of their float64 bytes plus every 16th value when
    # long; an outline's points are flattened (x0, y0, x1, ...).
    ns = 'xmlns="http://www.w3.org/2000/svg"'

    def f64(a) -> dict:
        a = np.ascontiguousarray(np.asarray(a, dtype="<f8"))
        out = {"n": int(a.size), "sha256": hashlib.sha256(a.tobytes()).hexdigest()}
        flat = a.ravel().tolist()
        if len(flat) <= 160:
            out["values"] = flat
        else:
            out["every"], out["sample"] = 16, flat[::16]
        return out

    def bits(mask) -> str:
        return "".join("1" if v else "0" for v in np.asarray(mask, bool).ravel())

    def trace(rel: str, **params) -> tuple[str, int, int]:
        img = load_upload((ROOT / "backend/bench" / rel).read_bytes(), max_bytes=1 << 30, max_pixels=1 << 30)
        return VexelEngine().trace(img, VexelParams(**params)).svg, img.width, img.height

    # ---------------------------------------------------------------- helpers
    def circle(cx, cy, r, n, wobble=0.0):
        t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
        rr = r + wobble * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        return np.column_stack([cx + rr * np.cos(t), cy + rr * np.sin(t)])

    def path(d: str) -> np.ndarray:
        return Q.path_polylines(d)[0][0]

    def walk(pieces, step=0.25) -> np.ndarray:
        """An outline drawn by turning: (length px, curvature 1/px) pieces from (10, 10), heading +x."""
        x, y, a = 10.0, 10.0, 0.0
        out = [(x, y)]
        for length, k in pieces:
            m = max(1, round(length / step))
            ds = length / m
            for _ in range(m):
                a += k * ds / 2
                x, y = x + ds * math.cos(a), y + ds * math.sin(a)
                a += k * ds / 2
                out.append((x, y))
        return np.array(out[:-1])

    wordmark, _, _ = trace("corpus/real/logo/vexel-wordmark-512.png")
    word = Q.parse(wordmark, (512, 512)).contours
    longest = sorted(range(len(word)), key=lambda i: -len(word[i].pts))
    t = np.linspace(0.0, 2 * np.pi, 400, endpoint=False)
    lem = np.column_stack([30 + 25 * np.cos(t) / (1 + np.sin(t) ** 2), 30 + 25 * np.sin(t) * np.cos(t) / (1 + np.sin(t) ** 2)])
    bean_r = 20 + 5 * np.cos(2 * t)
    star = np.array([[30 + (18 if i % 2 == 0 else 7) * math.cos(math.pi / 2 + i * math.pi / 5),
                      30 + (18 if i % 2 == 0 else 7) * math.sin(math.pi / 2 + i * math.pi / 5)] for i in range(10)])
    xs = np.linspace(0.0, 60.0, 121)
    outlines = [
        ("circle", circle(30, 30, 12, 96), True),
        ("circle, first point repeated", np.vstack([circle(30, 30, 12, 96), circle(30, 30, 12, 96)[:1]]), True),
        ("circle, open", circle(30, 30, 12, 96), False),
        ("large circle", circle(150, 150, 110, 720), True),
        ("rounded rect", Q._rect(5, 5, 40, 24, 5, 5), True),
        ("rounded rect, radii 2 2 2 6", path("M7 5 H39 A6 6 0 0 1 45 11 V27 A2 2 0 0 1 43 29 H7 A2 2 0 0 1 5 27 V7 A2 2 0 0 1 7 5 Z"), True),
        ("rect, top side bowed", path("M5 5 Q25 3.8 45 5 L45 29 L5 29 Z"), True),
        ("parallelogram, 3 degrees", path("M5 5 L45 7.1 L45 31.1 L5 29 Z"), True),
        ("trapezoid", path("M5 5 L45 5 L40 29 L10 29 Z"), True),
        ("chamfered rect", path("M8 5 H42 L45 8 V26 L42 29 H8 L5 26 V8 Z"), True),
        ("square, anticlockwise", np.array([[5.0, 5.0], [5.0, 35.0], [35.0, 35.0], [35.0, 5.0]]), True),
        ("bean", np.column_stack([30 + bean_r * np.cos(t), 30 + bean_r * np.sin(t)]), True),
        ("s-curve, open", np.column_stack([xs, 10 + 3 * np.sin(2 * np.pi * xs / 60.0)]), False),
        ("zigzag circle", circle(40, 40, 20, 250, 0.3), True),
        ("pinched sliver", np.array([[0.0, 0.0], [30.0, 0.25], [60.0, 0.0], [30.0, 0.5]]), True),
        ("long straight line", np.array([[0.0, 0.0], [80.0, 0.2]]), False),
        ("figure eight", lem, True),
        ("arc of 120 degrees, open", circle(20, 20, 15, 360)[:120], False),
        ("star", star, True),
        ("two points, open", np.array([[0.0, 0.0], [3.0, 4.0]]), False),
        ("two points, closed", np.array([[0.0, 0.0], [3.0, 4.0]]), True),
        ("tiny triangle", np.array([[0.0, 0.0], [0.05, 0.0], [0.0, 0.05]]), True),
        ("repeated and near-repeated points, open",
         np.array([[0, 0], [0, 0], [1e-10, 0], [5, 0], [5, 0], [5, 5 + 1e-12], [5, 5], [2, 7], [2, 7], [0, 0]], float), False),
        # kept, at 3e-8 px: the drop is at 1e-9
        ("a step of 3e-8 px, open", np.array([[0.0, 0.0], [0.0, 3e-8], [5.0, 0.0], [5.0, 5.0]]), False),
        # 40.5 steps long: round() ties to even
        ("a line of 10.125 px", np.array([[0.0, 0.0], [10.125, 0.0]]), False),
        ("a line of 10.375 px", np.array([[0.0, 0.0], [10.375, 0.0]]), False),
        # the last point 5e-6 and 1e-4 px from the first: np.allclose (1e-8 + 1e-5 * 42) takes them for one, so
        # nothing is appended; 5e-4 px is not close
        ("circle, last point 5e-6 px from the first", np.vstack([circle(30, 30, 12, 96), circle(30, 30, 12, 96)[:1] + [5e-6, 0.0]]), True),
        ("circle, last point 1e-4 px from the first", np.vstack([circle(30, 30, 12, 96), circle(30, 30, 12, 96)[:1] + [1e-4, 0.0]]), True),
        ("circle, last point 5e-4 px from the first", np.vstack([circle(30, 30, 12, 96), circle(30, 30, 12, 96)[:1] + [5e-4, 0.0]]), True),
        # small rectangles, whose corners' seeds tie: the stable argsort decides which is grown first
        ("small rect 4 x 10", np.array([[0.0, 0.0], [4.0, 0.0], [4.0, 10.0], [0.0, 10.0]]), True),
        ("small rect 5 x 10, anticlockwise", np.array([[0.0, 0.0], [5.0, 0.0], [5.0, 10.0], [0.0, 10.0]])[::-1], True),
        # corners grown over flanks that turn just over CURVED a sample (r = 24.9 px: 0.01004 rad),
        # and corners whose curve is longer than GROW_MAX
        ("rect, corners of 10 deg at r=24.9, 70 at r=10, 10 at r=24.9",
         walk([(30.0, 0.0)] + [(4.346, 1 / 24.9), (12.217, 0.1), (4.346, 1 / 24.9), (20.0, 0.0), (4.346, 1 / 24.9), (12.217, 0.1),
                               (4.346, 1 / 24.9), (30.0, 0.0)] + [(4.346, 1 / 24.9), (12.217, 0.1), (4.346, 1 / 24.9), (20.0, 0.0),
                                                                  (4.346, 1 / 24.9), (12.217, 0.1), (4.346, 1 / 24.9)]), True),
        ("triangle, corners longer than GROW_MAX",
         walk([(30.0, 0.0), (19.54, 1 / 24.875), (2.618, 0.2), (19.54, 1 / 24.875)] * 3), True),
        ("one point, closed", np.array([[5.0, 5.0]]), True),
        ("one point, open", np.array([[5.0, 5.0]]), False),
        ("wordmark contour a", word[longest[0]].pts, word[longest[0]].closed),
        ("wordmark contour b", word[longest[1]].pts, word[longest[1]].closed),
        ("wordmark contour c", word[longest[len(longest) // 2]].pts, word[longest[len(longest) // 2]].closed),
    ]
    helpers = []
    for name, pts, closed in outlines:
        q = Q._resample(pts, closed)
        entry = {"name": name, "closed": bool(closed), "pts": np.asarray(pts, float).ravel().tolist(), "q": f64(q)}
        if len(q) >= 3:
            entry["turns"] = {str(k): f64(Q._turns(q, closed, k)) for k in (1, 2, 16, 24)}
            entry["cancelled"] = f64(Q._cancelled(q, closed, 16))
            t24 = Q._turns(q, closed, 24)
            entry["flips"] = [list(map(int, Q._flips(t24, closed, math.radians(Q.INFLECT_HYST))[1])),
                              list(map(int, Q._flips(Q._turns(q, closed, 1), closed, 0.02)[1]))]
            entry["inflections"] = [int(j) for j in Q._inflections(q, closed, 24)]
            entry["area"] = Q._area(q)
            if closed:
                net = float(Q._turns(q, True, 1).sum())
                sign = 1.0 if net >= 0 else -1.0
                corners = Q._corners(q, sign)
                entry["net_sign"] = sign
                entry["corners"] = [{"at": list(c.at), "index": int(c.index), "lo": int(c.lo), "hi": int(c.hi),
                                     "turn_deg": float(c.turn_deg), "radius": float(c.radius)} for c in corners]
                vis = (np.arange(len(q)) // 7) % 3 != 0  # a stretch of every 21 samples off screen
                entry["rect_visible"] = bits(vis)
                entry["rect_like"] = [Q._rect_like(q, corners), Q._rect_like(q, corners, vis)]
        helpers.append(entry)
    assert any(e.get("rect_like", [None])[0] is not None for e in helpers)
    assert any(e.get("rect_like", [None])[0] is None and len(e.get("corners", [])) == 4 for e in helpers)
    assert any(e.get("inflections") for e in helpers) and any(e.get("flips", [[]])[0] for e in helpers)

    # _flips on made turnings of dyadic steps, so that the turning reaches the hysteresis exactly
    flip_cases = []
    for turn in ([0.25, 0.25, -0.5, 0.5, -0.25, -0.25, 0.5, 0.125, -0.625],
                 [0.5, -0.5, 0.5, -0.5, 0.5, -0.5],
                 [0.125] * 4 + [-0.125] * 8 + [0.125] * 4,
                 [0.0, 0.0, 0.0],
                 [-0.5, 0.25, 0.25, 0.25, -1.0, 1.5, -0.5]):
        for closed in (False, True):
            for hyst in (0.5, 0.25, 0.375):
                flip_cases.append({"turn": turn, "closed": closed, "hyst": hyst,
                                   "out": [int(j) for j in Q._flips(np.array(turn), closed, hyst)[1]]})
    assert any(c["out"] for c in flip_cases)

    rng = np.random.default_rng(11)
    dilate = []
    for i in range(40):
        n = int(rng.integers(25, 160))
        mask = rng.random(n) < [0.0, 0.02, 0.05, 0.2][i % 4]
        if i % 5 == 0:
            mask[0] = mask[-1] = True
        r = [0, 1, 3, 12][(i // 4) % 4]
        closed = i % 2 == 0
        dilate.append({"mask": bits(mask), "r": r, "closed": closed, "out": bits(Q._dilate(mask, r, closed))})
    wrap_in = np.concatenate([[0.0, -0.0, np.pi, -np.pi, 2 * np.pi, -2 * np.pi, 3 * np.pi, -3 * np.pi, 1e-17, -1e-17,
                               np.pi - 1e-16, -np.pi + 1e-16, 7.5, -7.5, 1e10, -1e10, 1e300, 5e-324],
                              rng.standard_normal(200) * 4])
    wrap = {"in": wrap_in.tolist(), "out": Q._wrap(wrap_in).tolist()}

    # visibility: made shapes on a 40 x 30 canvas, some off it, a stroke, a translucent cover
    vis_svg = (f'<svg {ns} viewBox="0 0 40 30"><rect x="-4" y="2" width="30" height="20" fill="#123"/>'
               '<circle cx="24" cy="16" r="9" fill="#456"/><path d="M2 26 L38 3" stroke="#789" stroke-width="3" fill="none"/>'
               '<rect x="30" y="-5" width="16" height="14" rx="3" fill="#abc" fill-opacity="0.4"/>'
               '<path d="M12 8 L30 8 L30 26 L12 26 Z M16 12 L26 12 L26 22 L16 22 Z" fill-rule="evenodd" fill="#def"/>'
               '<polyline points="1,1 8,5 3,12" stroke="#135" stroke-width="0.6" fill="none"/>'
               '<circle cx="50" cy="40" r="6" fill="#246"/>'
               # dyadic coordinates: x * 1000 is a tie, which np.round takes to even
               '<polyline points="3.0625,2.5625 10.0625,6.5625 14.4375,2.0625" stroke="#357" stroke-width="1.5" fill="none"/></svg>')
    drawing = Q.parse(vis_svg, (40, 30))
    visibility = {"svg": vis_svg, "size": [40, 30], "id_svg": Q.id_svg(drawing, (40, 30)), "scales": []}
    lookup_pts = np.array([[0.0, 0.0], [-0.1, 5.0], [5.0, -1e-9], [39.99, 29.99], [40.0, 3.0], [3.0, 30.0], [19.75, 14.25],
                           [1e300, 5.0], [-1e300, 5.0], [5.0, 1e18], [12.4999, 8.0], [12.5, 8.0], [24.0, 16.0], [-0.0, 7.0]])
    for scale in (1, 2, 3):
        ids = Q.id_map(drawing, (40, 30), scale)
        contours = []
        for c in drawing.contours:
            q = Q._resample(c.pts, c.closed)
            contours.append({"element": int(c.element), "closed": bool(c.closed), "stroke": c.stroke, "q": f64(q),
                             "visible": bits(Q.visible_samples(q, c.closed, c.element, c.stroke, ids, scale))})
        visibility["scales"].append({"scale": scale, "h": int(ids.shape[0]), "w": int(ids.shape[1]),
                                     "ids_sha256": hashlib.sha256(ids.astype("<i4").tobytes()).hexdigest(),
                                     "ids_counts": {str(k): int(v) for k, v in zip(*np.unique(ids, return_counts=True))},
                                     "lookup": Q._lookup(ids, lookup_pts * 1.0, scale).tolist(), "contours": contours})
    visibility["lookup_pts"] = lookup_pts.tolist()
    assert any("0" in c["visible"] and "1" in c["visible"] for s in visibility["scales"] for c in s["contours"])

    # np.dot as Accelerate computes it: `_area` dots a strided column with a rolled copy, and
    # `np.convolve` dots slices of a contiguous array with a copy of the kernel, which starts
    # 16-byte aligned (malloc) and is sliced at every offset. Random float64s, so that the order of
    # the additions and the fused multiply-adds show.
    dots = []
    for n in list(range(1, 41)) + [63, 64, 65, 100, 257]:
        x, y = rng.standard_normal(n) * 50, rng.standard_normal(n + 1) * 50
        y_aligned, y_shifted = np.array(y[:n]), np.array(y)[1:]  # a fresh array, and one 8 bytes into one
        assert y_aligned.ctypes.data % 16 == 0 and y_shifted.ctypes.data % 16 == 8
        q = rng.standard_normal((n, 2)) * 50
        dots.append({"x": x.tolist(), "y": y.tolist(), "aligned": float(np.dot(x, y_aligned)),
                     "shifted": float(np.dot(x, y_shifted)), "q": q.ravel().tolist(),
                     "strided": float(np.dot(q[:, 0], np.roll(q[:, 1], -1)))})
    convolve = []
    for n in (32, 33, 40, 77):
        a = rng.standard_normal(n)
        convolve.append({"a": a.tolist(), "out": np.convolve(a, np.ones(32), "same").tolist()})
    write("geometry_helpers", {"outlines": helpers, "flips": flip_cases, "dilate": dilate, "wrap": wrap,
                               "visibility": visibility, "dot": dots, "convolve": convolve})

    # ---------------------------------------------------------------- cards
    def plain(v):
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if isinstance(v, (int, np.integer)):
            return int(v)
        if isinstance(v, (float, np.floating)):
            return float(v)
        if isinstance(v, (list, tuple)):
            return [plain(x) for x in v]
        if isinstance(v, dict):
            return {k: plain(x) for k, x in v.items()}
        raise TypeError(type(v))

    cases = []

    def card(name: str, svg: str | None, size, vis: bool = True, id_scale: int = Q.ID_SCALE, file: str | None = None,
             repo: str | None = None):
        """One card. The SVG is a fixture file (`file`), a file in the repo (`repo`, relative to backend/)
        or inline."""
        if file is not None:
            (OUT / file).write_text(svg, encoding="utf-8")
        if repo is not None:
            svg = (ROOT / "backend" / repo).read_text(encoding="utf-8")
        entry = {"name": name, "file": file, "repo": repo, "svg": None if (file or repo) else svg,
                 "size": list(size) if size else None, "visibility": vis, "id_scale": id_scale,
                 "keys": None, "card": None, "error": None}
        try:
            got = Q.geometry_card(svg, tuple(size) if size else None, vis, id_scale)
            entry["keys"], entry["card"] = list(got), plain(got)
        except Exception as e:  # the Python raises; the port refuses
            entry["error"] = type(e).__name__
        cases.append(entry)

    for p in fixed_presets():
        svg, w, h = trace("corpus/real/logo/vexel-wordmark-512.png", **p.params)
        for vis in ((True, False) if p.id in ("balanced", "flat") else (True,)):
            card(f"wordmark {p.id}" + ("" if vis else ", everything visible"), svg, (w, h), vis, file=f"geometry_wordmark-{p.id}.svg")
    svg = (OUT / "geometry_wordmark-balanced.svg").read_text(encoding="utf-8")
    card("wordmark balanced, id map at 1x", svg, (512, 512), id_scale=1, file="geometry_wordmark-balanced.svg")
    card("wordmark balanced, id map at 3x", svg, (512, 512), id_scale=3, file="geometry_wordmark-balanced.svg")
    for rel in ("corpus/synthetic/shadow/card-512.png", "corpus/real/logo/logomark-128.png",
                "corpus/synthetic/logo/thin-mark-128.png", "corpus/real/logo/silverpeak-badge-768.png",
                "corpus/synthetic/flat/mosaic-512.png"):
        svg, w, h = trace(rel)
        card(f"trace of {rel}", svg, (w, h), file=f"geometry_{Path(rel).stem}.svg")
    card("trace of overlap-128, upsampled", (OUT / "drawing_overlap-128.svg").read_text(encoding="utf-8"), (128, 128))
    card("trace of overlap-128, no size", (OUT / "drawing_overlap-128.svg").read_text(encoding="utf-8"), None)
    for rel in ("noto/u1f4a0.svg", "noto/u2600.svg", "noto/u0030.svg", "fluent-flat/taurus.svg", "fluent-flat/mobile-phone.svg",
                "fluent-flat/fleur-de-lis.svg", "fluent-color/cherries.svg", "fluent-color/black-medium-small-square.svg",
                "fluent-color/spiral-notepad.svg"):
        card(f"held-out {rel}", None, (512, 512), repo=f"bench/heldout/{rel}")
    card("held-out fluent-flat/taurus.svg, no size", None, None, repo="bench/heldout/fluent-flat/taurus.svg")
    for rel in ("logo/ring.svg", "logo/tilted-squares.svg", "logo/thin-mark.svg", "logo/wedge-fan.svg", "shadow/radii.svg",
                "shadow/card.svg", "flat/stripes.svg", "flat/blobs.svg"):
        card(f"synthetic {rel}", None, (512, 512), repo=f"bench/corpus/synthetic/{rel}")

    def made(body: str, w: int = 64, h: int = 64) -> str:
        return f'<svg {ns} viewBox="0 0 {w} {h}">{body}</svg>'

    zig = "M8 32 " + " ".join(f"L{8 + i} {32 + (0.4 if i % 2 else -0.4)}" for i in range(1, 49)) + " L56 40 L8 40 Z"
    made_cases = {
        "made: rect with unequal radii": '<path d="M12 10 H48 A8 8 0 0 1 56 18 V46 A2 2 0 0 1 54 48 H10 A2 2 0 0 1 8 46 V14 A2 2 0 0 1 10 12 Z" fill="#c33"/>',
        "made: rect, one sharp corner and three round": '<path d="M8 8 H50 A6 6 0 0 1 56 14 V50 A6 6 0 0 1 50 56 H14 A6 6 0 0 1 8 50 Z" fill="#c33"/>',
        "made: bowed rect": '<path d="M8 10 Q32 6 56 10 L56 50 L8 50 Z" fill="#3c3"/>',
        "made: skewed rect": '<path d="M8 8 L56 10.5 L56 52.5 L8 50 Z" fill="#33c"/>',
        "made: pinched sliver": '<path d="M4 20 L30 20.3 L60 20 L30 20.6 Z" fill="#000"/>',
        "made: small shape": '<rect x="10" y="10" width="1.5" height="1.5" fill="#000"/>',
        "made: thin stroked line": '<path d="M4 30 L60 34" stroke="#000" stroke-width="0.5" fill="none"/>',
        "made: short thin stroke": '<path d="M4 30 L4.2 30.1" stroke="#000" stroke-width="0.5" fill="none"/>',
        "made: degenerate path": '<path d="M10 10 L40 40 Z" fill="#000"/><path d="M5 5 L5 5" fill="#111"/>',
        "made: hidden shape": '<rect x="10" y="10" width="30" height="30" fill="#c33"/><rect x="5" y="5" width="50" height="50" fill="#33c"/>',
        "made: translucent cover": '<rect x="10" y="10" width="30" height="30" fill="#c33"/><rect x="5" y="5" width="50" height="50" fill="#33c" opacity="0.3"/>',
        "made: s-curve": '<path d="M4 32 C20 8 44 56 60 32 L60 60 L4 60 Z" fill="#c3c"/>',
        "made: wobble": f'<path d="{zig}" fill="#3cc"/>',
        "made: strokes and fills": '<circle cx="32" cy="32" r="20" fill="none" stroke="#000" stroke-width="2"/><polyline points="4,4 20,8 12,30" stroke="#000" stroke-width="0.8" fill="none"/><line x1="0" y1="60" x2="64" y2="62" stroke="#000"/>',
        "made: shapes off the canvas": '<circle cx="-40" cy="-40" r="10" fill="#000"/><rect x="70" y="10" width="20" height="20" fill="#000"/><circle cx="64" cy="64" r="12" fill="#333"/>',
        "made: nothing": "",
        "made: a star and a use": '<defs><path id="s" d="M32 6 L39 25 L59 25 L43 37 L49 57 L32 45 L15 57 L21 37 L5 25 L25 25 Z" fill="#fc3"/></defs><use href="#s"/><use href="#s" x="3" y="2" fill-opacity="0.2"/>',
        "made: one-point polyline": '<polyline points="5,5" stroke="#000" stroke-width="0.3" fill="none"/>',
        "made: far away coordinates": '<path d="M1e9 1e9 L1000000010 1e9 L1000000010 1000000010 Z" fill="#000"/><circle cx="32" cy="32" r="9"/>',
        # NaN and infinite points, which numpy carries through (a NaN step is dropped, and so is the next)
        "made: an infinite scale": '<rect width="10" height="10" transform="matrix(1e999 0 0 1 0 0)"/>',
        "made: an infinite translation": '<rect width="10" height="10" transform="translate(1e999 0)"/><circle cx="30" cy="30" r="9"/>',
        "made: a stroke under an infinite scale": '<path d="M0 0 L10 0 L10 10 Z" transform="matrix(1e999 0 0 1e999 0 0)" stroke="#000" stroke-width="0.5"/>',
        "made: a stroke to infinity": '<path d="M5 5 L1e999 5" stroke="#000" stroke-width="0.5" fill="none"/>',
        "made: a scale of 1e-300": '<g transform="scale(1e-300)"><rect width="10" height="10"/></g>',
        "made: a sliver of area 0.045, just degenerate": '<path d="M10 10 L19 10 L10 10.01 Z" fill="#000"/>',
        "made: a sliver of area 0.055": '<path d="M10 10 L21 10 L10 10.01 Z" fill="#000"/>',
        # an open stroke that wobbles at both ends, its start painted over: what lies past its ends counts as its end does
        "made: a wobbly stroke, one end covered": '<path d="' + zig.split(" L56")[0] + '" stroke="#000" stroke-width="2" fill="none"/>'
                                                  '<rect x="0" y="20" width="14" height="24" fill="#c33"/>',
    }
    for name, body in made_cases.items():
        card(name, made(body), (64, 64))
    card("made: hidden shape, everything visible", made(made_cases["made: hidden shape"]), (64, 64), vis=False)
    card("made: no size", made(made_cases["made: hidden shape"]), None)
    card("made: an infinite stroke width", made('<path d="M4 4 L60 60" stroke="#000" stroke-width="1e999" fill="none"/>'
                                                '<rect x="20" y="4" width="30" height="20" fill="#c33"/>'), (64, 64))
    # a square of 62 500 px a side drawn with a million points, 0.25 px apart (1e6 samples, mostly off
    # the canvas): the Rust test builds the same text from `square`
    side = 250_000

    def quarter(v: int) -> str:
        return f"{v // 4}.{(v % 4) * 25:02d}"

    ring = ([(i, 0) for i in range(side)] + [(side, i) for i in range(side)] + [(side - i, side) for i in range(side)]
            + [(0, side - i) for i in range(side)])
    big = made('<path d="M' + " ".join(f"{quarter(x)} {quarter(y)}" for x, y in ring) + ' Z" fill="#c33"/>'
               '<circle cx="32" cy="32" r="10" fill="#33c"/>')
    card("made: a square of a million points", big, (64, 64))
    cases[-1]["svg"], cases[-1]["square"] = None, side
    # what the Python raises on, which the port refuses
    card("made: a polygon of no points", made('<polygon points="" fill="#000"/>'), (64, 64))
    card("made: a length that overflows", made('<path d="M0 0 L1e308 0 L-1e308 0 Z" fill="#000"/>'), (64, 64))
    card("made: an infinite radius", made('<circle cx="10" cy="10" r="1e999" fill="#000"/>'), (64, 64))
    card("made: an infinite rect", made('<rect x="0" y="0" width="1e999" height="10" fill="#000"/>'), (64, 64))
    card("made: not xml", "<svg", (64, 64))

    # Each threshold of the card with a made case just either side of it (the measured values
    # are the Python's, found by sweeping the shape).
    def rounded(big: float, small: float) -> str:
        """A 48 px square with three corners of radius `big` and the top-left one of `small`."""
        b, r = big, small
        return (f'<path d="M{8 + b} 8 H{56 - b} A{b} {b} 0 0 1 56 {8 + b} V{56 - b} A{b} {b} 0 0 1 {56 - b} 56 '
                f'H{8 + b} A{b} {b} 0 0 1 8 {56 - b} V{8 + r} A{r} {r} 0 0 1 {8 + r} 8 Z" fill="#c33"/>')

    def tilted(deg: float) -> str:
        dy = 48 * math.tan(math.radians(deg))
        return f'<path d="M8 8 L56 {8 + dy:.4f} L56 {50 + dy:.4f} L8 50 Z" fill="#33c"/>'

    def spread(dr: float) -> str:
        r, r2 = 4.0, 4.0 + dr
        return (f'<path d="M{8 + r} 8 H{56 - r2} A{r2} {r2} 0 0 1 56 {8 + r2} V{56 - r} A{r} {r} 0 0 1 {56 - r} 56 '
                f'H{8 + r} A{r} {r} 0 0 1 8 {56 - r} V{8 + r} A{r} {r} 0 0 1 {8 + r} 8 Z" fill="#c33"/>')

    def arc_top(deg: float) -> str:
        r = 24 / math.sin(math.radians(deg / 2))
        return f'<path d="M8 12 A{r:.4f} {r:.4f} 0 0 1 56 12 L56 56 L8 56 Z" fill="#c33"/>'

    thresholds = {
        "edge: bow 0.240 px (RECT_BOW 0.25)": '<path d="M8 10 Q32 9.26 56 10 L56 50 L8 50 Z" fill="#3c3"/>',
        "edge: bow 0.260 px": '<path d="M8 10 Q32 9.2 56 10 L56 50 L8 50 Z" fill="#3c3"/>',
        "edge: skew 0.9 deg (RECT_SKEW 1)": tilted(0.9),
        "edge: skew 1.1 deg": tilted(1.1),
        "edge: skew 5.5 deg (RECT_GRID 6)": tilted(5.5),
        "edge: skew 6.5 deg": tilted(6.5),
        "edge: radii spread 0.96 px (RADIUS_SPREAD 1)": spread(0.9),
        "edge: radii spread 1.13 px": spread(1.1),
        "edge: radii 1.59 and 0.64, mixed (ROUND_R 1.5, SHARP_R 0.75)": rounded(1.55, 0.7),
        "edge: radii 1.43 and 0.64, not mixed": rounded(1.45, 0.7),
        "edge: radii 1.75 and 0.80, not mixed": rounded(1.75, 0.8),
        "edge: a disc of 3.95 px2 (SLIVER_AREA 4)": '<circle cx="20" cy="20" r="1.13" fill="#000"/>',
        "edge: a disc of 4.12 px2": '<circle cx="20" cy="20" r="1.15" fill="#000"/>',
        "edge: corners whose window turns 46.8 deg (CORNER_SEED_DEG 45)": '<rect x="4" y="4" width="56" height="56" rx="12.25" fill="#c33"/>',
        "edge: corners whose window turns 43.3 deg": '<rect x="4" y="4" width="56" height="56" rx="13.25" fill="#c33"/>',
        "edge: a side turning 28 deg (RECT_SIDE_DEG 30)": arc_top(28.0),
        "edge: a side turning 32 deg": arc_top(32.0),
        "edge: a 2 px square, perimeter 8.0 (2 * WOBBLE_SCALE)": '<rect x="10" y="10" width="2" height="2" fill="#c33"/>',
        "edge: a short thin stroke under a later shape": '<path d="M20 20 L20.2 20.1" stroke="#000" stroke-width="0.5" fill="none"/>'
                                                         '<rect x="10" y="10" width="20" height="20" fill="#c33"/>',
    }
    for name, body in thresholds.items():
        card(name, made(body), (64, 64))
    edge = {c["name"]: c["card"] for c in cases if c["name"].startswith("edge:")}
    expect = [("edge: bow 0.240 px (RECT_BOW 0.25)", "rect_bowed", 0), ("edge: bow 0.260 px", "rect_bowed", 1),
              ("edge: skew 0.9 deg (RECT_SKEW 1)", "rect_skewed", 0), ("edge: skew 1.1 deg", "rect_skewed", 1),
              ("edge: skew 5.5 deg (RECT_GRID 6)", "rect_like", 1), ("edge: skew 6.5 deg", "rect_like", 0),
              ("edge: radii spread 0.96 px (RADIUS_SPREAD 1)", "radius_inconsistent", 0), ("edge: radii spread 1.13 px", "radius_inconsistent", 1),
              ("edge: radii 1.59 and 0.64, mixed (ROUND_R 1.5, SHARP_R 0.75)", "radius_inconsistent", 1),
              ("edge: radii 1.43 and 0.64, not mixed", "radius_inconsistent", 0), ("edge: radii 1.75 and 0.80, not mixed", "radius_inconsistent", 0),
              ("edge: a disc of 3.95 px2 (SLIVER_AREA 4)", "slivers", 1), ("edge: a disc of 4.12 px2", "slivers", 0),
              ("edge: corners whose window turns 46.8 deg (CORNER_SEED_DEG 45)", "rect_like", 1),
              ("edge: corners whose window turns 43.3 deg", "rect_like", 0),
              ("edge: a side turning 28 deg (RECT_SIDE_DEG 30)", "rect_like", 1), ("edge: a side turning 32 deg", "rect_like", 0),
              ("edge: a short thin stroke under a later shape", "thin_strokes", 0)]
    for name, key, want in expect:
        assert edge[name][key] == want, (name, key, edge[name][key])
    assert edge["edge: a 2 px square, perimeter 8.0 (2 * WOBBLE_SCALE)"]["outline_len_px"] == 8.0

    ok = [c["card"] for c in cases if c["card"] is not None]
    # The golden must not be vacuous: every counter and every location list is set off somewhere.
    for key in ("strokes", "outline_len_px", "hidden_len_px", "slivers", "sliver_area_px", "degenerate", "thin_strokes",
                "wobble_deg_100px", "inflections", "rect_like", "radius_inconsistent", "rect_bowed", "rect_skewed",
                "_wobble_at", "_flips_at", "_slivers_at", "_radius_at"):
        assert any(c[key] for c in ok), f"no case sets off {key}"
    assert any(c["_slivers_at"] and any(s[2] == 0.0 for s in c["_slivers_at"]) for c in ok), "no thin stroke located"
    assert [c["error"] for c in cases if c["error"]] == ["IndexError", "OverflowError", "OverflowError", "ParseError"]
    write("geometry", cases)


@exporter("scorecard")
def _scorecard() -> None:
    import hashlib
    import io
    import math

    import numpy as np
    from PIL import Image

    from studi0trace.engines.presets import auto_candidates
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging import quality as Q
    from studi0trace.imaging.intake import load_upload

    # quality.Reference, assess, scorecard, artifact_index and is_clean. `scorecard.json` holds
    # the cases: whole assessments of traces (the wordmark with each Auto candidate, whose SVGs
    # the Auto tests score again, held-out items, a source with transparency, a non-square one,
    # a source above 640 x 640 that is scored at 2x), scorecards with their options (detail,
    # visibility, id and hole scales, a given opaque mask), made SVGs that set off each counter
    # against a source made from them, the refusals, and cards made by hand for the weights
    # and thresholds of `artifact_index` and `is_clean`. Each case names its source (a file of
    # the repo, or a PNG written beside the fixtures) and its SVG (a fixture file, or inline).
    # The Rust test builds the Reference from the pixels the intake decodes, so a JPEG source is
    # written out as the PNG of the pixels Pillow decoded: the decoders differ by a level.
    ns = 'xmlns="http://www.w3.org/2000/svg"'
    cases: list[dict] = []

    def plain(v):
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if isinstance(v, (int, np.integer)):
            return int(v)
        if isinstance(v, (float, np.floating)):
            return float(v)
        if isinstance(v, (list, tuple)):
            return [plain(x) for x in v]
        if isinstance(v, dict):
            return {k: plain(x) for k, x in v.items()}
        raise TypeError(type(v))

    def digest(a) -> str:
        return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()

    def decoded(data: bytes) -> np.ndarray:
        return np.asarray(load_upload(data, max_bytes=1 << 30, max_pixels=1 << 30).image, dtype=np.uint8)

    def trace(rgba: np.ndarray, **params) -> str:
        img = load_upload(_png(rgba), max_bytes=1 << 30, max_pixels=1 << 30)
        return VexelEngine().trace(img, VexelParams(**params)).svg

    def _png(rgba: np.ndarray) -> bytes:
        out = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(out, "PNG")
        return out.getvalue()

    def source_of(repo: str | None, png: str | None, rgba: np.ndarray | None = None, jpeg_as_png: str | None = None):
        """(entry fields, rgba): a file of the repo (relative to backend/), a PNG written here from `rgba`
        (named `png`), or the PNG of what Pillow decoded from a file of the repo that is not lossless."""
        if repo is not None and jpeg_as_png is None:
            rgba = decoded((ROOT / "backend" / repo).read_bytes())
            fields = {"source_repo": repo, "source_file": None}
        else:
            name = jpeg_as_png or png
            if rgba is None:
                rgba = decoded((ROOT / "backend" / repo).read_bytes())
            (OUT / name).write_bytes(_png(rgba))
            assert np.array_equal(decoded((OUT / name).read_bytes()), rgba)
            fields = {"source_repo": None, "source_file": name}
        h, w = rgba.shape[:2]
        return {**fields, "width": int(w), "height": int(h), "rgba_sha256": digest(rgba)}, rgba

    def svg_fields(name: str, svg: str, file: bool | str) -> dict:
        """The SVG inline, in a fixture file of its own (`file` True), or in one an earlier case wrote (`file` its name)."""
        if isinstance(file, str):
            assert (OUT / file).read_text(encoding="utf-8") == svg
            return {"svg_file": file, "svg": None}
        if file:
            (OUT / f"scorecard_{name}.svg").write_text(svg, encoding="utf-8")
            return {"svg_file": f"scorecard_{name}.svg", "svg": None}
        return {"svg_file": None, "svg": svg}

    def reference_fields(ref: "Q.Reference") -> dict:
        return {"edges_sha256": digest(ref.edges.astype(np.uint8)), "edges_wide_sha256": digest(ref.edges_wide.astype(np.uint8)),
                "opaque_sha256": digest(ref.opaque.astype(np.uint8)), "edges": int(ref.edges.sum()), "opaque": int(ref.opaque.sum())}

    def case(name: str, kind: str, src: dict, rgba: np.ndarray, svg: str, file: bool | str = False, **options) -> dict:
        """One case: `assess` (options: hole_scale) or `scorecard` (detail, visibility, hole_scale, id_scale,
        opaque = pass the Reference's mask)."""
        ref = Q.Reference(rgba)
        entry = {"name": name, "kind": kind, **src, **svg_fields(name, svg, file), "options": options,
                 "reference": reference_fields(ref), "keys": None, "card": None, "clean": None, "error": None}
        try:
            if kind == "assess":
                got = Q.assess(svg, ref, options.get("hole_scale"))
            else:
                kw = {k: v for k, v in options.items() if k not in ("opaque", "hole_scale") or (k == "hole_scale" and v is not None)}
                kw.pop("opaque", None)
                got = Q.scorecard(svg, rgba, opaque=ref.opaque if options.get("opaque") else None, **kw)
            entry["keys"], entry["card"], entry["clean"] = list(got), plain(got), bool(Q.is_clean(got))
        except Exception as e:  # the Python raises; the port refuses
            entry["error"] = type(e).__name__
        cases.append(entry)
        return entry

    def trace_case(name: str, repo: str, preset: str | None = None, params: dict | None = None, file: bool = True,
                   jpeg: bool = False, **options) -> dict:
        src, rgba = source_of(repo, None, jpeg_as_png=f"scorecard_{name}_source.png" if jpeg else None)
        return case(name, "assess", src, rgba, trace(rgba, **(params or {})), file=file, **options)

    # ---------------------------------------------------------------- traces
    wordmark = "bench/corpus/real/logo/vexel-wordmark-512.png"
    for p in auto_candidates():  # Task 13 scores the same SVGs under these names
        trace_case(f"auto_{p.id}", wordmark, params=p.params)
    held = "bench/heldout/"
    trace_case("heldout_wheelchair", held + "fluent-color/man-in-motorized-wheelchair-facing-right-512.png")
    trace_case("heldout_u1f693", held + "noto/u1f693-512.png")
    trace_case("heldout_taurus", held + "fluent-flat/taurus-512.png")
    trace_case("heldout_u2049_jpeg", held + "noto/u2049-512-q75.jpg", jpeg=True)
    trace_case("transparent_alpha_fade", "bench/corpus/synthetic/gradient/alpha-fade-512.png")
    trace_case("non_square_logomark", "bench/corpus/real/logo/logomark-512.png")
    big = trace_case("large_silverpeak_768", "bench/corpus/real/logo/silverpeak-badge-768.png")
    assert big["width"] * big["height"] > 640 * 640

    # the options of scorecard on a real trace: the id map's scale decides what is on screen
    src, rgba = source_of(wordmark, None)
    word_svg = (OUT / "scorecard_auto_balanced.svg").read_text(encoding="utf-8")
    id_cards = [case(f"wordmark_id_scale_{k}", "scorecard", src, rgba, word_svg, file="scorecard_auto_balanced.svg", detail=False,
                     visibility=True, hole_scale=4, id_scale=k)["card"] for k in (1, 3)]
    id_cards.append(next(c for c in cases if c["name"] == "auto_balanced")["card"])
    assert len({json.dumps({k: v for k, v in c.items() if k in ("hidden_len_px", "outline_len_px", "wobble_deg_100px", "inflections")}) for c in id_cards}) == 3, \
        "the id scale decides nothing on this trace"

    # ---------------------------------------------------------------- made SVGs
    def made(body: str, w: int = 64, h: int = 64) -> str:
        return f'<svg {ns} viewBox="0 0 {w} {h}">{body}</svg>'

    def from_svg(name: str, truth: str, svg: str, w: int, h: int, kind: str = "assess", **options) -> dict:
        """A source drawn from `truth` (what the trace was meant to reproduce) and `svg`, the trace."""
        src, rgba = source_of(None, f"scorecard_{name}.png", Q.render(truth, w, h))
        return case(name, kind, src, rgba, svg, **options)

    flat = made('<rect width="64" height="64" fill="#c33"/>')
    holes = made('<path fill-rule="evenodd" fill="#c33" d="M0 0 H64 V64 H0 Z M10 10 h0.6 v0.6 h-0.6 Z M20 20 h0.12 v0.12 h-0.12 Z M30 30 h0.6 v0.6 h-0.6 Z"/>')
    seam = made('<rect width="31.8" height="64" fill="#c33"/><rect x="32.4" width="31.6" height="64" fill="#c33"/>')
    from_svg("made_pinholes_detail", flat, holes, 64, 64, "scorecard", detail=True, visibility=True, hole_scale=4, id_scale=2)
    from_svg("made_pinholes", flat, holes, 64, 64)
    from_svg("made_seam", flat, seam, 64, 64)
    from_svg("made_seam_scorecard_8x", flat, seam, 64, 64, "scorecard", detail=False, visibility=True, hole_scale=8, id_scale=2, opaque=True)

    # every counter at once: a 2 x 2 sheet of the geometry card's made shapes
    zig = "M8 32 " + " ".join(f"L{8 + i} {32 + (0.4 if i % 2 else -0.4)}" for i in range(1, 49)) + " L56 40 L8 40 Z"
    cells = [
        '<path d="M12 10 H48 A8 8 0 0 1 56 18 V46 A2 2 0 0 1 54 48 H10 A2 2 0 0 1 8 46 V14 A2 2 0 0 1 10 12 Z" fill="#c33"/>',
        '<path d="M8 10 Q32 6 56 10 L56 50 L8 50 Z" fill="#3c3"/><path d="M4 56 L30 56.3 L60 56 L30 56.6 Z" fill="#000"/>',
        '<path d="M8 8 L56 10.5 L56 52.5 L8 50 Z" fill="#33c"/><path d="M4 60 L60 63" stroke="#000" stroke-width="0.5" fill="none"/>',
        f'<path d="{zig}" fill="#3cc"/><path d="M4 8 C20 -10 44 26 60 8 L60 20 L4 20 Z" fill="#c3c"/><path d="M10 10 L40 40 Z" fill="#000"/>',
    ]
    sheet = made("".join(f'<g transform="translate({64 * (i % 2)} {64 * (i // 2)})">{c}</g>' for i, c in enumerate(cells)), 128, 128)
    from_svg("made_every_defect", sheet, sheet, 128, 128)
    from_svg("made_every_defect_detail", sheet, sheet, 128, 128, "scorecard", detail=True, visibility=True, hole_scale=4, id_scale=2)
    from_svg("made_every_defect_everything_visible", sheet, sheet, 128, 128, "scorecard", detail=False, visibility=False, hole_scale=4, id_scale=3)

    # a shape under another: scored when it is on screen, and when everything is
    hidden = made('<rect x="10" y="10" width="30" height="30" fill="#c33"/><rect x="5" y="5" width="50" height="50" fill="#33c"/>')
    shown = [from_svg(f"made_hidden_shape{tag}", hidden, hidden, 64, 64, "scorecard", detail=True, visibility=vis, hole_scale=4, id_scale=2)["card"]
             for tag, vis in (("", True), ("_everything_visible", False))]
    assert shown[0]["hidden_len_px"] > 0 and shown[1]["hidden_len_px"] == 0 and shown[0]["outline_len_px"] < shown[1]["outline_len_px"]

    # a source with nothing opaque: no hole count, and the geometry is scored all the same
    clear = made("")
    from_svg("made_transparent_source", clear, sheet, 128, 128, "scorecard", detail=True, visibility=True, hole_scale=4, id_scale=2)
    # sources too small for an opaque interior or an edge
    for side in (1, 2, 3):
        from_svg(f"made_source_{side}px", flat, made('<rect width="64" height="64" fill="#c33"/>'), side, side)

    # 640 x 640 is scored at 4x, 641 x 640 and 640 x 641 at 2x, and so are the 409 600 pixels of 800 x 512 and
    # 512 x 800 at 4x: the seam's sub-pixels tell which. An explicit hole scale wins over the size.
    for w, h in ((640, 640), (641, 640), (640, 641), (800, 512), (512, 800)):
        truth = made('<rect width="640" height="640" fill="#c33"/>', w, h)
        trace = made('<rect width="299.8" height="640" fill="#c33"/><rect x="300.4" width="339.6" height="640" fill="#c33"/>', w, h)
        e = from_svg(f"made_{w}x{h}", truth, trace, w, h)
        rgba = decoded((OUT / e["source_file"]).read_bytes())
        at = {k: Q.holes(trace, rgba, k)["hole_subpx"] for k in (2, 4)}
        assert at[2] != at[4] and e["card"]["hole_subpx"] == at[4 if w * h <= 640 * 640 else 2], (w, h, at, e["card"]["hole_subpx"])
    from_svg("made_641x640_at_4x", made('<rect width="640" height="640" fill="#c33"/>', 641, 640),
             made('<rect width="299.8" height="640" fill="#c33"/><rect x="300.4" width="339.6" height="640" fill="#c33"/>', 641, 640),
             641, 640, hole_scale=4)
    from_svg("made_640x640_at_2x", made('<rect width="640" height="640" fill="#c33"/>', 640, 640),
             made('<rect width="299.8" height="640" fill="#c33"/><rect x="300.4" width="339.6" height="640" fill="#c33"/>', 640, 640),
             640, 640, hole_scale=2)

    # ---------------------------------------------------------------- refusals
    flat_src, flat_rgba = source_of(None, "scorecard_made_refusals.png", Q.render(flat, 64, 64))
    clear_src, clear_rgba = source_of(None, "scorecard_made_refusals_clear.png", Q.render(clear, 64, 64))
    case("refused_not_xml_opaque_source", "assess", flat_src, flat_rgba, "<svg")
    case("refused_not_xml_clear_source", "scorecard", clear_src, clear_rgba, "<svg", detail=False, visibility=True, hole_scale=4, id_scale=2)
    case("refused_hole_scale_0", "scorecard", flat_src, flat_rgba, flat, detail=False, visibility=True, hole_scale=0, id_scale=2)
    case("clear_source_hole_scale_0_is_zeros", "scorecard", clear_src, clear_rgba, flat, detail=False, visibility=True, hole_scale=0, id_scale=2)

    ok = [c for c in cases if c["card"] is not None]
    assert [c["name"] for c in cases if c["error"]] == ["refused_not_xml_opaque_source", "refused_not_xml_clear_source", "refused_hole_scale_0"], [
        (c["name"], c["error"]) for c in cases if c["error"]]

    # ---------------------------------------------------------------- cards made by hand
    zero = {k: 0 for k in Q.ARTIFACT_KEYS if k not in ("wobble_deg_100px", "segments_100px", "artifact_index")}
    zero.update({"wobble_deg_100px": 0.0, "segments_100px": 0.0})
    hand = []

    def card(name: str, **over) -> None:
        c = {**zero, **over}
        entry = {"name": name, "card": c, "index": None, "clean": None}
        entry["index"], entry["clean"] = Q.artifact_index(c), bool(Q.is_clean(c))
        hand.append(entry)

    card("nothing")
    for w in (0.0, 1.0, 24.99, 24.999999, 25.0, 25.000001, 25.01, 100.0, 213.6698):
        card(f"wobble {w}", wobble_deg_100px=w)
    card("one pinhole", pinholes=1, hole_clusters=1)
    card("a cluster that is not a pinhole", hole_clusters=1)
    card("5 clusters, 2 pinholes", hole_clusters=5, pinholes=2)
    card("fewer clusters than pinholes", hole_clusters=1, pinholes=3)
    card("as many clusters as pinholes", hole_clusters=4, pinholes=4)
    for key in ("slivers", "degenerate", "thin_strokes", "radius_inconsistent", "rect_bowed", "rect_skewed", "inflections"):
        card(f"one {key}", **{key: 1})
    card("three of each rect defect", radius_inconsistent=3, rect_bowed=3, rect_skewed=3)
    card("counts that are not defects", rect_like=40, elements=300, segments=9000, strokes=12, segments_100px=33.3)
    card("every term different", pinholes=2, hole_clusters=7, slivers=3, degenerate=5, thin_strokes=7, radius_inconsistent=11,
         rect_bowed=13, rect_skewed=17, wobble_deg_100px=19.5, inflections=23)
    card("every term different, clean wobble", pinholes=0, hole_clusters=9, slivers=0, degenerate=0, thin_strokes=0, radius_inconsistent=1,
         rect_bowed=2, rect_skewed=4, wobble_deg_100px=24.4, inflections=29)
    card("a trace", **{k: v for k, v in ok[0]["card"].items() if k not in ("artifact_index",) and not k.startswith("_")})
    assert [h["clean"] for h in hand if h["name"].startswith("wobble")] == [True, True, True, True, False, False, False, False, False]
    # a card with no rect_skewed, as older results hold: is_clean reads it as 0
    old = {k: v for k, v in zero.items() if k != "rect_skewed"}
    missing = [{"name": "no rect_skewed", "card": old, "clean": bool(Q.is_clean(old))},
               {"name": "no rect_skewed, a bowed rect", "card": {**old, "rect_bowed": 1}, "clean": bool(Q.is_clean({**old, "rect_bowed": 1}))}]

    # ---------------------------------------------------------------- the golden must not be vacuous
    for key in ("pinholes", "hole_clusters", "slivers", "sliver_area_px", "degenerate", "thin_strokes", "wobble_deg_100px", "inflections",
                "rect_like", "radius_inconsistent", "rect_bowed", "rect_skewed", "strokes", "hidden_len_px", "hole_subpx", "hole_px"):
        assert any(c["card"][key] for c in ok), f"no case sets off {key}"
    assert any(c["card"]["edge_f1"] < 1 for c in ok if "edge_f1" in c["card"]), "no case has an edge_f1 under 1"
    assert any(c["card"]["edge_f1"] == 0.0 for c in ok if "edge_f1" in c["card"]), "no case has an edge_f1 of 0"
    assert any(c["card"]["_clusters"] for c in ok if "_clusters" in c["card"]), "no detail case lists clusters"
    for key in ("_wobble_at", "_flips_at", "_slivers_at", "_radius_at"):
        assert any(c["card"][key] for c in ok if key in c["card"]), f"no detail case lists {key}"
    assert any(c["clean"] for c in ok) and any(not c["clean"] for c in ok)
    assert any(c["card"]["pinholes"] and c["card"]["hole_clusters"] > c["card"]["pinholes"] for c in ok), "no case has a cluster that is not a pinhole"
    assert {h["clean"] for h in hand} == {True, False}
    assert all(math.isfinite(h["index"]) for h in hand)

    data = {"keys": list(Q.ARTIFACT_KEYS), "cases": cases, "hand": hand, "missing_rect_skewed": missing,
            "hole_scale": {"hole_scale": Q.HOLE_SCALE, "id_scale": Q.ID_SCALE}}
    json.dumps(data, allow_nan=False)  # a NaN or an infinity in a card would not read back
    write("scorecard", data)


@exporter("auto")
def _auto() -> None:
    import hashlib
    import math
    import random
    import struct

    import numpy as np
    from fastapi.testclient import TestClient

    from studi0trace import auto as A
    from studi0trace.api.schemas import AutoCandidate, AutoResult, CandidateScores, ErrorBody
    from studi0trace.engines.presets import auto_candidates
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging import quality as Q
    from studi0trace.imaging.intake import load_upload
    from studi0trace.main import create_app
    from studi0trace.settings import Settings

    # Auto: the rule (`choose`, `faithful`), the words (`issues`, `summary`), Python's `round` and the whole
    # of `POST /vectorize auto=true` on real images. Nothing here is written by hand: every expectation is
    # what the Python answers, the real route included (its candidates run on threads, as they ship).
    rng = random.Random(20260930)

    def bits(x: float) -> str:
        return "0x%016x" % struct.unpack("<Q", struct.pack("<d", x))[0]

    def fl(x: float):
        """A float as JSON holds it, or a word for what it cannot."""
        if math.isnan(x):
            return "nan"
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
        return x

    # ------------------------------------------------------------------ round
    # `round(x, n)` is the decimal string of the exact binary value, correctly rounded (ties to even), read
    # back: not `floor(x * 10^n + 0.5)`, and not half-even on the decimal the programmer typed: 0.35 is
    # 0.34999999999999997 and rounds to 0.3, 0.45 is 0.4500000000000000111 and rounds to 0.5.
    xs: list[float] = []
    for _ in range(150):  # decimals as typed: m / 10^d, none exactly representable
        d = rng.randint(1, 5)
        xs.append(rng.randint(0, 10 ** (d + rng.randint(0, 2))) / 10 ** d)
    for _ in range(150):  # exact ties for 0, 1, 2 and 4 digits: k / 2^j and k + 1/2
        j = rng.randint(1, 6)
        xs.append(rng.randint(0, 40 * 2 ** j) / 2 ** j)
    for _ in range(60):
        xs.append(rng.randint(0, 20000) + 0.5)
    for x in list(xs[150:360]):  # a tie and the doubles either side of it
        xs += [math.nextafter(x, math.inf), math.nextafter(x, -math.inf)]
    for _ in range(100):
        xs.append(rng.uniform(0, 10 ** rng.randint(0, 4)))
    for _ in range(40):
        xs.append(10 ** rng.uniform(-300, -4))
    for _ in range(40):
        xs.append(10 ** rng.uniform(15, 300))
    xs += [0.0, 1.0, 5e-324, 1.7976931348623157e308, 2.5, 3.5, 0.5, 1.5, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 2.675, 1.005, 8.345, 0.125, 0.375]
    xs += [-x for x in rng.sample(xs, 250)] + [-0.0, -5e-324, -0.04, -0.05, -0.0049, float("nan"), float("inf"), float("-inf")]
    seen_bits: set[str] = set()
    rounds = []
    for x in xs:
        if bits(x) in seen_bits:
            continue
        seen_bits.add(bits(x))
        rounds.append([bits(x)] + [bits(round(x, n)) for n in (0, 1, 2, 4)])
    # the ones the plan's first guess got wrong, by name
    named = {name: [bits(x), n, bits(round(x, n))] for name, (x, n) in {
        "0.25 to 1 is a tie and goes to the even 0.2": (0.25, 1),
        "0.35 is just under the tie and goes down": (0.35, 1),
        "0.45 is just over the tie and goes up": (0.45, 1),
        "0.05 is just over and goes up": (0.05, 1),
        "0.15 is just under and goes down": (0.15, 1),
        "2.5 to 0 is a tie and goes to 2": (2.5, 0),
        "3.5 to 0 is a tie and goes to 4": (3.5, 0),
        "2.675 to 2": (2.675, 2),
        "a small negative keeps its sign": (-0.04, 1),
        "nan stays nan": (float("nan"), 1),
    }.items()}
    assert named["0.35 is just under the tie and goes down"][2] == bits(0.3) and named["0.45 is just over the tie and goes up"][2] == bits(0.5)
    assert named["0.25 to 1 is a tie and goes to the even 0.2"][2] == bits(0.2) and named["a small negative keeps its sign"][2] == bits(-0.0)
    assert len(rounds) > 800, len(rounds)

    # ------------------------------------------------------------------ choose
    def S(id, de, f1=0.99, art=0.0, el=10):  # noqa: N802
        return A.Scored(id, de, f1, art, el)

    def ident(scored, s):
        return next(i for i, x in enumerate(scored) if x is s)

    def case_of(scored: list) -> dict:
        entry = {"scored": [[s.id, fl(s.delta_e), fl(s.edge_f1), fl(s.artifact_index), s.elements] for s in scored],
                 "faithful": None, "pick": None, "reason": None, "raises": None}
        try:
            entry["faithful"] = [ident(scored, s) for s in A.faithful(scored)]
        except ValueError:
            entry["faithful"] = "raises"
        try:
            pick, why = A.choose(scored)
            entry["pick"], entry["reason"] = (None if pick is None else ident(scored, pick)), why
        except ValueError as e:
            entry["raises"], entry["reason"] = type(e).__name__, str(e)
        return entry

    names = ["balanced", "logo", "detailed", "dense", "flat", "x"]
    arts = [0.0, 0.04, 0.05, 0.06, 0.1, 0.14, 0.15, 0.16, 0.24, 0.25, 0.26, 0.34, 0.35, 0.36, 0.45, 0.55, 0.65, 0.75, 1.0, 1.05, 2.0, 2.04,
            2.05, 2.5, 3.0, 12.0, 12.05, 12.049999999999999]

    def random_set() -> list:
        n = rng.randint(1, 6)
        best = rng.choice([0.0, 0.01, 0.1, 0.2, 0.4, 0.5, 1.0, 1.5, 2.5, 10.0, rng.uniform(0, 5)])
        limit = A.de_limit(best)
        edge0 = rng.choice([1.0, 0.99, 0.95, 0.8, 0.5])
        edge_edge = edge0 - A.EDGE_SLACK

        def de() -> float:
            r = rng.random()
            if r < 0.15:
                return best
            if r < 0.30:
                return limit
            if r < 0.38:
                return math.nextafter(limit, math.inf)
            if r < 0.46:
                return math.nextafter(limit, -math.inf)
            if r < 0.70:
                return round(rng.uniform(best, limit), 2)
            return best + rng.uniform(0, 3 * (limit - best) + 0.5)

        def f1() -> float:
            r = rng.random()
            if r < 0.30:
                return edge0
            if r < 0.45:
                return edge_edge
            if r < 0.52:
                return math.nextafter(edge_edge, math.inf)
            if r < 0.59:
                return math.nextafter(edge_edge, -math.inf)
            if r < 0.80:
                return edge0 - rng.choice([0.01, 0.03, 0.05, 0.2])
            return rng.uniform(0.3, 1.0)

        des = [de() for _ in range(n)]
        des[rng.randrange(n)] = best
        ids = [rng.choice(names) for _ in range(n)] if rng.random() < 0.1 else rng.sample(names, n)
        return [S(ids[i], des[i], f1(), rng.choice(arts) if rng.random() < 0.8 else round(rng.uniform(0, 30), rng.choice([1, 2, 6])),
                  rng.choice([1, 2, 3, 5, 5, 12, 12, 20])) for i in range(n)]

    choose_cases = [case_of(random_set()) for _ in range(300)]
    # the numbers of the unit tests and a few shapes the random sets reach rarely
    nan = float("nan")
    for scored in (
        [], [S("a", 0.40)], [S("a", 0.4), S("a", 0.4)], [S("a", 0.4, art=2.0), S("b", 0.4, art=2.0), S("a", 0.4, art=2.0)],
        [S("balanced", 0.40, art=12.0), S("logo", 0.50, art=3.0), S("dense", 0.52, art=1.0)],
        [S("balanced", 0.40, art=12.0), S("dense", 0.56, art=0.0)],
        [S("a", 1.0, art=9.0), S("b", 1.29, art=1.0)], [S("a", 1.0, art=9.0), S("b", 1.31, art=1.0)],
        [S("a", 0.40, f1=0.99), S("b", 0.45, f1=0.96), S("c", 0.45, f1=0.975)],
        # NaN, which no render produces: where the Python picks, the port picks the same; where it raises, the port says no one
        [S("a", 0.4, art=nan), S("b", 0.45, art=1.0)], [S("b", 0.45, art=1.0), S("a", 0.4, art=nan)],
        [S("a", 0.4, art=nan), S("b", 0.4, art=nan)], [S("a", 0.4, art=nan, el=3), S("b", 0.4, art=nan, el=1), S("c", 0.4, art=0.0)],
        [S("a", 0.4, art=1.0), S("b", 0.4, art=nan), S("c", 0.4, art=0.5)],
        [S("a", nan), S("b", 0.45)], [S("a", 0.4), S("b", nan)], [S("a", 0.4), S("b", nan), S("c", 0.41)],
        [S("a", 0.4, f1=nan), S("b", 0.45, f1=0.9)], [S("a", 0.4, f1=0.9), S("b", 0.45, f1=nan)],
        [S("a", float("inf")), S("b", float("inf"))], [S("a", 0.4, art=float("inf")), S("b", 0.45, art=float("inf"))],
    ):
        choose_cases.append(case_of(scored))
    reasons = {}
    for c in choose_cases:
        if c["raises"] is None:
            reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
    for r in ("the only candidate that traced", "the only one this faithful to the image", "the most faithful, and the cleanest",
              "as clean at the same fidelity, with fewer shapes", "the cleanest at the same fidelity",
              "the most faithful; the cleaner ones lose detail", "the cleanest of the most faithful"):
        assert reasons.get(r, 0) >= 8, f"the random sets reach {r!r} only {reasons.get(r, 0)} times"
    assert reasons["no candidate could be scored"] == 1  # the empty set
    # the band's edges and the edge-F1 slack's are on the table in earnest, as are rounding ties
    at_limit = at_edge = dup_ids = 0
    for c in choose_cases:
        rows = [[float(v) if not isinstance(v, str) else float(v) for v in r[1:4]] for r in c["scored"]]
        if len(rows) > 1 and all(math.isfinite(v) for r in rows for v in r):
            lim = A.de_limit(min(r[0] for r in rows))
            at_limit += any(r[0] == lim for r in rows)
            ok = [r for r in rows if r[0] <= lim]
            at_edge += any(r[1] == max(o[1] for o in ok) - A.EDGE_SLACK for r in ok)
        dup_ids += len({r[0] for r in c["scored"]}) < len(c["scored"])
    assert at_limit >= 25 and at_edge >= 25 and dup_ids >= 10, (at_limit, at_edge, dup_ids)
    print("choose:", len(choose_cases), "cases;", sum(c["raises"] is not None for c in choose_cases), "raise; reasons", reasons,
          "; at the limit", at_limit, "at the edge slack", at_edge, "duplicate ids", dup_ids)
    assert sum(c["raises"] is not None for c in choose_cases) >= 2

    # ------------------------------------------------------------------ issues, summary
    def random_card() -> dict:
        c = {
            "delta_e_mean": rng.choice([rng.uniform(0, 5), 0.41234, 0.12345, 0.00005, 0.00015, 0.5, 0.0, 12.34565, rng.uniform(0, 1e-3)]),
            "edge_f1": rng.choice([rng.uniform(0, 1), 1.0, 0.98, 0.98765, 0.00005, 0.0]),
            "artifact_index": rng.choice([rng.uniform(0, 80), 0.75, 0.125, 0.375, 2.675, 1.005, 0.0, 12.345, 3.0]),
            "elements": rng.choice([0, 1, 9, 57, 300]),
            "pinholes": rng.choice([0, 0, 1, 2, 5, 11]),
            "slivers": rng.choice([0, 0, 1, 3]), "degenerate": rng.choice([0, 0, 1, 2]), "thin_strokes": rng.choice([0, 0, 1, 4]),
            "wobble_deg_100px": rng.choice([0.0, 3.0, 24.99999999999999, 25.0, 25.000000000000004, 24.95, 24.949999999999996, 0.05, 0.25, 0.35, 40.0, 213.6698,
                                            rng.uniform(0, 80)]),
            "radius_inconsistent": rng.choice([0, 0, 1, 3]), "rect_bowed": rng.choice([0, 0, 1, 2]), "rect_skewed": rng.choice([0, 0, 1, 2]),
            "inflections": rng.choice([0, 1, 2, 3, 4, 10]),
        }
        if rng.random() < 0.2:
            del c["rect_skewed"]  # older results do not have it
        return c

    cards = [random_card() for _ in range(300)]
    # every threshold alone, either side
    zero = {"delta_e_mean": 0.3, "edge_f1": 1.0, "artifact_index": 0.0, "elements": 4, "pinholes": 0, "slivers": 0, "degenerate": 0,
            "thin_strokes": 0, "wobble_deg_100px": 0.0, "radius_inconsistent": 0, "rect_bowed": 0, "rect_skewed": 0, "inflections": 0}
    cards.append(dict(zero))
    for key, vals in (("pinholes", (1, 2)), ("slivers", (1, 2)), ("degenerate", (1, 2)), ("thin_strokes", (1, 2)), ("radius_inconsistent", (1, 2)),
                      ("rect_bowed", (1, 2)), ("rect_skewed", (1, 2)), ("inflections", (2, 3)),
                      ("wobble_deg_100px", (24.999999999999996, 25.0, 25.000000000000004))):
        cards += [dict(zero, **{key: v}) for v in vals]
    cards.append({k: v for k, v in zero.items() if k != "rect_skewed"})
    cards.append({**{k: v for k, v in zero.items() if k != "rect_skewed"}, "rect_bowed": 1})
    issue_cases = []
    for card in cards:  # the numbers go to the port bit for bit, so a tie rounds the same way in both
        got = A.summary(card)
        issue_cases.append({"card": card, "issues": A.issues(card), "summary": got})
    seen_issues = {w.split(" ", 1)[-1] if w[0].isdigit() else w for c in issue_cases for w in c["issues"]}
    assert {"pinhole", "pinholes", "sliver", "slivers", "wobbly edges", "uneven rectangle", "uneven rectangles", "wavy curves"} <= seen_issues, seen_issues
    assert any(c["summary"]["clean"] for c in issue_cases) and any(not c["summary"]["clean"] for c in issue_cases)

    # ------------------------------------------------------------------ the route, on real images
    client = TestClient(create_app(Settings()), raise_server_exceptions=True)
    key_order = {m.__name__: list(m.model_fields) for m in (AutoResult, AutoCandidate, CandidateScores, ErrorBody)}
    images = []
    wordmark = "bench/corpus/real/logo/vexel-wordmark-512.png"
    for name, repo in (("wordmark", wordmark),
                       ("multi_shape_128", "bench/corpus/synthetic/gradient/multi-shape-128.png"),
                       ("linear_4stop_128", "bench/corpus/synthetic/gradient/linear-4stop-128.png"),
                       ("venn_128", "bench/corpus/synthetic/logo/venn-128.png"),
                       ("blobs_128", "bench/corpus/synthetic/flat/blobs-128.png"),
                       ("wedge_fan_128", "bench/corpus/synthetic/logo/wedge-fan-128.png"),
                       ("studi0trace_mark_128", "bench/corpus/real/logo/studi0trace-mark-128.png")):
        data = (ROOT / "backend" / repo).read_bytes()
        body = client.post("/vectorize", files={"file": ("x.png", data, "application/octet-stream")},
                           data={"engines": "vexel", "auto": "true"}).json()
        res = body["auto"]["vexel"]
        img = load_upload(data, max_bytes=1 << 30, max_pixels=1 << 30)
        ref = A.reference(img)
        rgba = np.asarray(img.image.convert("RGBA"), dtype=np.uint8)
        cands, fixtures = [], {}
        for c, p in zip(res["candidates"], auto_candidates()):
            assert c["preset"] == p.id and c["error"] is None and c["scores"] is not None
            # what the route answers is what the engine and the scorecard give on their own
            svg = VexelEngine().trace(img, VexelParams(**p.params)).svg
            assert svg == c["svg"], f"{name} {p.id}: the route's SVG is not the engine's"
            card = A.assess(svg, ref)
            assert A.summary(card) == c["scores"], (name, p.id)
            if name == "wordmark":
                assert (OUT / f"scorecard_auto_{p.id}.svg").read_text(encoding="utf-8") == svg
                svg_file = f"scorecard_auto_{p.id}.svg"
            else:
                svg_file = f"auto_{name}_{p.id}.svg"
                (OUT / svg_file).write_text(svg, encoding="utf-8")
            assert c["elapsed_ms"] > 0
            cands.append({k: v for k, v in c.items() if k not in ("svg", "elapsed_ms")} | {"svg_file": svg_file})
            fixtures[p.id] = {"card": {k: (v if not isinstance(v, (np.integer, np.floating, np.bool_)) else v.item()) for k, v in card.items()},
                              "summary": A.summary(card)}
            # the rounded numbers are not within a rounding error of their edge
            for key, n in (("delta_e_mean", 4), ("edge_f1", 4), ("artifact_index", 2), ("wobble_deg_100px", 1)):
                x = card[key] * 10 ** n
                assert abs(x - math.floor(x) - 0.5) > 1e-6, (name, p.id, key, card[key])
        # the response built from the models, to give the shape its order
        shaped = AutoResult(engine=res["engine"], pick=res["pick"], reason=res["reason"], candidates=[
            AutoCandidate(**{k: v for k, v in c.items() if k != "svg_file"} | {"scores": CandidateScores(**c["scores"])}) for c in cands]).model_dump()
        assert [c["preset"] for c in shaped["candidates"]] == [p.id for p in auto_candidates()]
        assert body["results"]["vexel"]["svg"] == next(c for c in res["candidates"] if c["preset"] == res["pick"])["svg"]
        assert body["parameters_used"]["vexel"] == next(c for c in res["candidates"] if c["preset"] == res["pick"])["parameters"]
        # and the rule on its own, over the scores the route kept, agrees with the route
        scored = [A.Scored(c["preset"], f["card"]["delta_e_mean"], f["card"]["edge_f1"], f["card"]["artifact_index"], int(f["card"]["elements"]))
                  for c, f in zip(cands, fixtures.values())]
        pick, why = A.choose(scored)
        assert (pick.id, why) == (res["pick"], res["reason"])
        images.append({
            "name": name, "source_repo": repo, "width": img.width, "height": img.height,
            "rgba_sha256": hashlib.sha256(np.ascontiguousarray(rgba).tobytes()).hexdigest(),
            "pick": res["pick"], "reason": res["reason"], "engine": res["engine"], "candidates": cands,
            "scored": fixtures, "chosen_svg_file": next(c["svg_file"] for c in cands if c["preset"] == res["pick"]),
            "parameters_used": body["parameters_used"]["vexel"],
        })
    assert len({i["pick"] for i in images}) >= 4, "the images pick the same few presets: the end-to-end test would not tell a rule from a constant"
    assert len({i["reason"] for i in images}) >= 5, {i["reason"] for i in images}

    data = {
        "constants": {"DE_SLACK": A.DE_SLACK, "DE_SHARE": A.DE_SHARE, "EDGE_SLACK": A.EDGE_SLACK},
        "round": {"digits": [0, 1, 2, 4], "cases": rounds, "named": named},
        "choose": choose_cases, "issues": issue_cases, "images": images, "key_order": key_order,
    }
    json.dumps(data, allow_nan=False)
    write("auto", data)


@exporter("api")
def _api() -> None:
    import base64
    import hashlib
    import io
    import re

    from fastapi.testclient import TestClient
    from PIL import Image

    from studi0trace.main import create_app
    from studi0trace.settings import Settings

    # The five calls the frontend makes (`getHealth`, `getEngines`, `getPresets`, `uploadImage`, `vectorize`) and
    # the failures the routes raise, as the FastAPI app answers them: status and body, the real route included.
    # Nothing is written by hand. The Rust `Core` answers the same calls and its JSON is compared as a STRING (so a
    # key out of its place fails), after both sides are normalised the same way:
    #   svg         -> its SHA-256 (the traces are byte-identical; a hash keeps the fixture small)
    #   elapsed_ms  -> null (a wall time)
    #   image_id    -> "<image_id>" (the Python's is uuid4().hex, random; the Rust one is a content hash; both are
    #                  32 lowercase hex digits, which the test checks apart)
    #   version     -> "<version>" (the crate's own)
    # The Python app serves three engines; the core describes one, so `engines` keeps only Vexel.
    ID_FORMAT = r"^[0-9a-f]{32}$"
    wordmark = "bench/corpus/real/logo/vexel-wordmark-512.png"  # relative to backend/, as the other fixtures name a corpus image
    mark = "bench/corpus/real/logo/studi0trace-mark-128.png"
    sources: dict[str, dict] = {"wordmark": {"file": wordmark}, "mark128": {"file": mark}}
    data_of = {"wordmark": (ROOT / "backend" / wordmark).read_bytes(), "mark128": (ROOT / "backend" / mark).read_bytes()}
    # images Auto picks the 2nd, 3rd and 4th candidate for (auto.json): a route that promoted the first would not show on the others
    picky = {"multi_shape128": "bench/corpus/synthetic/gradient/multi-shape-128.png",
             "linear_4stop128": "bench/corpus/synthetic/gradient/linear-4stop-128.png",
             "venn128": "bench/corpus/synthetic/logo/venn-128.png"}
    for key, file in picky.items():
        sources[key] = {"file": file}
        data_of[key] = (ROOT / "backend" / file).read_bytes()

    def inline(name: str, data: bytes) -> None:
        sources[name] = {"b64": base64.b64encode(data).decode("ascii")}
        data_of[name] = data

    inline("garbage", b"definitely not an image")
    inline("empty", b"")
    inline("corrupt_png", data_of["mark128"][: len(data_of["mark128"]) * 6 // 10])
    tiff = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(tiff, "TIFF")
    inline("tiff", tiff.getvalue())

    def normal(node, *, svg: bool = True):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "svg" and isinstance(v, str):
                    out[k] = hashlib.sha256(v.encode("utf-8")).hexdigest()
                elif k == "elapsed_ms":
                    out[k] = None
                elif k == "image_id" and isinstance(v, str):
                    assert re.fullmatch(ID_FORMAT, v), v
                    out[k] = "<image_id>"
                else:
                    out[k] = normal(v)
            return out
        if isinstance(node, list):
            return [normal(v) for v in node]
        return node

    def seen_times(node) -> list[float]:
        found = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "elapsed_ms" and v is not None:
                    found.append(v)
                else:
                    found += seen_times(v)
        elif isinstance(node, list):
            for v in node:
                found += seen_times(v)
        return found

    def make_client(limits: dict | None):
        return TestClient(create_app(Settings(**(limits or {}))), raise_server_exceptions=True)

    default_client = make_client(None)
    cases: list[dict] = []

    def record(name: str, call: str, response, **args) -> dict:
        body = response.json()
        times = seen_times(body)
        assert all(t > 0 for t in times), (name, times)
        case = {"name": name, "call": call, **args, "status": response.status_code, "body": normal(body)}
        cases.append(case)
        return case

    # ------------------------------------------------------------------ health, engines, presets
    r = default_client.get("/health")
    body = r.json()
    assert body["engines"][0] == "vexel" and body["vexel"] in ("rust", "python") and list(body) == ["status", "version", "engines", "vexel"]
    python_engines = list(body["engines"])
    body["engines"] = ["vexel"]
    body["version"] = "<version>"
    body["vexel"] = "rust"  # the core is the Rust pipeline; `VEXEL_BACKEND=python` would say otherwise of the server
    cases.append({"name": "health", "call": "health", "status": r.status_code, "body": body})

    r = default_client.get("/engines")
    engines = [e for e in r.json() if e["id"] == "vexel"]
    assert len(engines) == 1
    cases.append({"name": "engines", "call": "engines", "status": r.status_code, "body": engines})

    r = default_client.get("/presets")
    cases.append({"name": "presets", "call": "presets", "status": r.status_code, "body": r.json()})

    # ------------------------------------------------------------------ uploads
    def post_upload(client, key: str):
        return client.post("/uploads", files={"file": ("x.png", data_of[key], "application/octet-stream")})

    for key in ("wordmark", "mark128", "garbage", "empty", "corrupt_png", "tiff"):
        record(f"upload_{key}", "upload", post_upload(default_client, key), source=key)
    small_bytes = {"max_bytes": 1000, "max_pixels": 40_000_000}
    small_pixels = {"max_bytes": 20 * 1024 * 1024, "max_pixels": 100_000}
    record("upload_too_large", "upload", post_upload(make_client({"max_upload_bytes": 1000}), "wordmark"), source="wordmark", limits=small_bytes)
    record("upload_too_many_pixels", "upload", post_upload(make_client({"max_image_pixels": 100_000}), "wordmark"), source="wordmark", limits=small_pixels)

    # ------------------------------------------------------------------ vectorize
    ids: dict[str, str] = {}
    for key in ("wordmark", "mark128", *picky):
        ids[key] = post_upload(default_client, key).json()["image_id"]

    def post_vectorize(image_id: str, parameters, auto: bool):
        form = {"image_id": image_id, "engines": "vexel", "parameters": json.dumps({"vexel": parameters})}
        if auto:
            form["auto"] = "true"
        return default_client.post("/vectorize", data=form)

    def vec(name: str, source: str | None, parameters, auto: bool = False, image_id: str | None = None) -> dict:
        response = post_vectorize(ids[source] if source else image_id, parameters, auto)
        args = {"image": source} if source else {"image_id": image_id}
        return record(name, "vectorize", response, **args, parameters=parameters, auto=auto)

    changed_wordmark = {"detail": 12.5, "min_region": 20, "gradients": False, "layering": "cutout", "curve_tolerance": 0.8,
                        "path_precision": 3, "shape_fitting": False, "strokes": False, "shadows": False}
    changed_mark = {"upsample": "always", "detail": 10, "min_region": 16.0, "max_stops": 6, "corner_threshold": 90,
                    "overlaps": False, "stroke_tolerance": 0.2, "curve_tolerance": 0.25}
    ok = [
        vec("default_wordmark", "wordmark", {}),
        vec("default_mark128", "mark128", {}),
        vec("changed_wordmark", "wordmark", changed_wordmark),
        vec("changed_mark128", "mark128", changed_mark),
        vec("refined_wordmark", "wordmark", {"refine": True}),
        vec("auto_wordmark", "wordmark", {}, auto=True),
        vec("auto_mark128", "mark128", {}, auto=True),
        # Auto validates the parameters it then ignores, and `parameters_used` is the pick's own
        vec("auto_with_parameters_mark128", "mark128", {"detail": 12.5}, auto=True),
    ]
    picks = {}
    for key in picky:
        case = vec(f"auto_{key}", key, {}, auto=True)
        ok.append(case)
        picks[key] = case["body"]["auto"]["vexel"]["pick"]
    assert picks == {"multi_shape128": "logo", "linear_4stop128": "detailed", "venn128": "dense"}, picks
    for case in ok[-3:]:  # the pick is what `results` and `parameters_used` carry, not the first candidate
        a = case["body"]["auto"]["vexel"]
        chosen = next(c for c in a["candidates"] if c["preset"] == a["pick"])
        assert case["body"]["results"]["vexel"]["svg"] == chosen["svg"] and case["body"]["parameters_used"]["vexel"] == chosen["parameters"]
        assert chosen is not a["candidates"][0]
    # `parameters or {}`: whatever is falsy is the defaults, and whatever else is not a dict is refused
    for label, value in (("null", None), ("zero", 0), ("zero_float", 0.0), ("false", False), ("empty_string", ""), ("empty_list", [])):
        ok.append(vec(f"falsy_{label}_mark128", "mark128", value))
    for case in ok:
        assert case["status"] == 200 and case["body"]["success"] is True, case["name"]
    assert all(c["body"] == ok[1]["body"] for c in ok[-6:]), "a falsy value is the defaults"
    assert ok[2]["body"]["parameters_used"]["vexel"]["detail"] == 12.5 and ok[3]["body"]["parameters_used"]["vexel"]["detail"] == 10.0
    # refinement moves the wordmark's curves (on the 128 px mark it nudges nothing): the flag reached the engine
    assert ok[4]["body"]["results"]["vexel"]["svg"] != ok[0]["body"]["results"]["vexel"]["svg"]
    assert ok[5]["body"]["auto"]["vexel"]["pick"] and ok[5]["body"]["results"]["vexel"]["svg"] == next(
        c["svg"] for c in ok[5]["body"]["auto"]["vexel"]["candidates"] if c["preset"] == ok[5]["body"]["auto"]["vexel"]["pick"])

    refused = [
        ("range_low", {"detail": 0.5}),
        ("range_high", {"min_region": 201}),
        ("range_float_bound", {"stroke_tolerance": 0.04}),
        ("range_corner", {"corner_threshold": 151}),
        ("float_null", {"detail": None}),
        ("float_list", {"detail": [1]}),
        ("bool_null", {"gradients": None}),
        ("int_fraction", {"min_region": 16.5}),
        ("int_null", {"path_precision": None}),
        ("choice_wrong", {"layering": "sideways"}),
        ("choice_number", {"upsample": 3}),
        ("unknown_field", {"colour": 3}),
        ("several", {"colour": {"a": 1}, "detail": 0, "layering": "x", "max_stops": 9}),
        ("not_an_object_string", "abc"),
        ("not_an_object_number", 5),
        ("not_an_object_list", [1]),
        ("not_an_object_true", True),
    ]
    for name, value in refused:
        case = vec(f"refused_{name}", "mark128", value)
        assert case["status"] == 422 and isinstance(case["body"]["detail"], list), (name, case["status"])
    case = vec("refused_with_auto", "mark128", {"detail": 0.5}, auto=True)
    assert case["status"] == 422
    # the parameters are checked before the upload is looked up
    case = vec("refused_before_expired", None, {"detail": 0.5}, image_id="0" * 32)
    assert case["status"] == 422

    vec("expired", None, {}, image_id="0" * 32)
    vec("expired_auto", None, {}, auto=True, image_id="0" * 32)
    vec("expired_garbage_id", None, {}, image_id="not-an-id")
    vec("no_image", None, {}, image_id="")
    assert [c["status"] for c in cases if c["name"] in ("expired", "expired_auto", "expired_garbage_id", "no_image")] == [404, 404, 404, 400]

    write("api", {
        "image_id_format": ID_FORMAT,
        "python_engines": python_engines,
        "sources": sources,
        "cases": cases,
    }, sort_keys=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    unknown = sorted(only - EXPORTERS.keys())
    if unknown:
        sys.exit(f"unknown exporter {', '.join(unknown)}; known: {', '.join(sorted(EXPORTERS))}")
    for name, fn in EXPORTERS.items():
        if not only or name in only:
            fn()
            print("wrote", name)


if __name__ == "__main__":
    main()
