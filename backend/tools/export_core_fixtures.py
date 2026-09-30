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
                              "stroke": None if c.stroke is None else num(c.stroke), **points(c.pts)}
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
    for path in sorted(OUT.glob("render_*.svg")) + [ROOT / "backend/bench/heldout" / h for h in sorted(heldout)]:
        svg = path.read_text(encoding="utf-8")
        stem = path.stem.removeprefix("render_")
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
