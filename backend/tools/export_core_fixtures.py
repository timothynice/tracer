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


def write(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


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
