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
