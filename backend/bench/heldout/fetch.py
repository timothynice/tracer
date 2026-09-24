"""Fetch and build the held-out corpus: real emoji artwork Vexel was never developed on.

    cd backend && .venv/bin/python -m bench.heldout.fetch            # list, sample, download, build
    cd backend && .venv/bin/python -m bench.heldout.fetch --build     # rebuild rasters from the SVGs on disk

Two upstream sets, each pinned to one commit, listed once through the GitHub
API's git/trees endpoint and downloaded file by file from
raw.githubusercontent.com at that commit:

  fluent-flat   Microsoft Fluent Emoji, "Flat" style      MIT
  fluent-color  Microsoft Fluent Emoji, "Color" style     MIT     (linear/radial gradients)
  noto          Google Noto Emoji, 2D/svg/                Apache-2.0

No item is hand-picked. Every set's candidate paths are filtered by path rule
alone (below), sorted, and shuffled with `random.Random(f"{SEED}:{set}")`;
candidates are then taken in that order and each is downloaded and kept
unless it fails a content rule, until the set's quota is met. A skipped file
is logged in `fetch_log.json` with the rule it failed. An emoji already taken
by one Fluent set is not taken again by the other, so no subject appears twice.

Path rules (applied to the listing, before anything is downloaded):
  fluent  assets/<Name>/Flat/*.svg or assets/<Name>/Color/*.svg; of the
          skin-tone emoji only the Default tone (so an emoji counts once)
  noto    2D/svg/emoji_u*.svg without a Fitzpatrick modifier (U+1F3FB-1F3FF)

Content rules (applied to the downloaded file):
  - well-formed XML with a square viewBox (the rasters are square)
  - no <text>, <image>, <foreignObject> or <script>: text depends on the
    fonts installed and an embedded bitmap is not vector truth
  - no external reference (href to http/https/file)
  - resvg (the bench's renderer) renders it, and the render is not blank

Filters, clips, masks and group opacity are kept: they are the artwork, and
resvg's render of them is the truth the bench scores against. Items carrying
them are tagged `has:filter` etc. so they can be split out.

Each kept SVG yields three items, built exactly the way `bench/corpus` builds
its own (bench.synth.render_png / render_downsampled, and the q75 JPEGs of
`bench/corpus/real/*-q75.jpg`, which are the 512 px render flattened on white
and saved by Pillow at quality 75 with its default 4:2:0 subsampling):

  <set>/<slug>-512.png      clean 512 px resvg render (RGBA)
  <set>/<slug>-512-ds.png   rendered at 1024 px and bilinearly downsampled
  <set>/<slug>-512-q75.jpg  the clean render on white, JPEG q75

All three carry the SVG as `truth_svg` (on white the JPEG's truth is the same
drawing). `manifest.yaml` is the bench's manifest format:
`python -m bench run --corpus bench/heldout ...`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image

HERE = Path(__file__).resolve().parent
SEED = 20260924
SIZE = 512

REPOS = {
    "fluent": {
        "repo": "microsoft/fluentui-emoji",
        "sha": "1ffb34c752ecf5d402f04cfb4b392c77f57c54bc",
        "tree": "assets",          # listed recursively
        "license": "MIT",
        "copyright": "Copyright (c) Microsoft Corporation.",
        "project": "Microsoft Fluent Emoji — https://github.com/microsoft/fluentui-emoji",
    },
    "noto": {
        "repo": "googlefonts/noto-emoji",
        "sha": "06121655d0e82f9cae6e7ba6feed4fa6fdbfc2a4",
        "tree": "2D/svg",          # one directory, listed flat
        "license": "Apache-2.0",
        "copyright": "Copyright Google LLC and the Noto Emoji authors (AUTHORS, CONTRIBUTORS in the project)",
        "project": "Google Noto Emoji — https://github.com/googlefonts/noto-emoji",
    },
}
SETS = {  # set id -> (repo key, quota)
    "fluent-flat": ("fluent", 10),
    "fluent-color": ("fluent", 10),
    "noto": ("noto", 20),
}
TONES = ("Light", "Medium-Light", "Medium", "Medium-Dark", "Dark")
FITZPATRICK = re.compile(r"_1f3f[b-f]", re.I)
UA = {"User-Agent": "studi0trace-bench-heldout/1"}


# ---------------------------------------------------------------- listing and sampling

def _get(url: str, accept: str | None = None) -> bytes:
    headers = dict(UA)
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read()


def list_tree(key: str, cache: Path | None) -> list[str]:
    """Every blob path under the repo's `tree` directory at the pinned commit (one API call)."""
    spec = REPOS[key]
    cached = cache / f"tree-{key}-{spec['sha'][:12]}.json" if cache else None
    if cached and cached.exists():
        data = json.loads(cached.read_text())
    else:
        ref = urllib.parse.quote(f"{spec['sha']}:{spec['tree']}", safe="")
        url = f"https://api.github.com/repos/{spec['repo']}/git/trees/{ref}" + ("?recursive=1" if key == "fluent" else "")
        data = json.loads(_get(url, "application/vnd.github+json"))
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data))
    if data.get("truncated"):
        raise SystemExit(f"{spec['repo']}: the tree listing came back truncated; the sample would be biased")
    return sorted(f"{spec['tree']}/{e['path']}" for e in data["tree"] if e["type"] == "blob")


def candidates(set_id: str, paths: list[str]) -> list[str]:
    if set_id.startswith("fluent"):
        style = "Flat" if set_id == "fluent-flat" else "Color"
        out = []
        for p in paths:
            parts = p.split("/")
            if not p.endswith(".svg") or style not in parts[:-1]:
                continue
            # assets/<Name>/<Style>/x.svg, or assets/<Name>/<Tone>/<Style>/x.svg for skin-tone emoji
            if len(parts) == 4 and parts[2] == style:
                out.append(p)
            elif len(parts) == 5 and parts[3] == style and parts[2] == "Default":
                out.append(p)
        return sorted(out)
    return sorted(p for p in paths if re.fullmatch(r"2D/svg/emoji_u[0-9a-f_]+\.svg", p) and not FITZPATRICK.search(p))


def subject(set_id: str, path: str) -> str:
    """What the file depicts, for the no-repeats rule and for SOURCES.md."""
    if set_id.startswith("fluent"):
        return path.split("/")[1]
    cps = [int(c, 16) for c in Path(path).stem.removeprefix("emoji_u").split("_")]
    names = [unicodedata.name(chr(c), f"U+{c:04X}") for c in cps if c not in (0x200D, 0xFE0F)]
    return " + ".join(n.title() for n in names)


def slug(set_id: str, path: str) -> str:
    if set_id.startswith("fluent"):
        return re.sub(r"[^a-z0-9]+", "-", path.split("/")[1].lower()).strip("-")
    return Path(path).stem.removeprefix("emoji_").replace("_", "-").lower()


# ---------------------------------------------------------------- content rules

_FORBIDDEN = ("text", "image", "foreignObject", "script")
_EXTERNAL = re.compile(r'(?:xlink:)?href\s*=\s*"(?:https?:|file:|//)', re.I)
_FEATURES = {"gradient": ("linearGradient", "radialGradient"), "filter": ("filter",), "clip": ("clipPath",),
             "mask": ("mask",), "pattern": ("pattern",)}


def check(svg: str) -> tuple[str | None, list[str]]:
    """(reason it is skipped or None, feature tags)."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return f"not well-formed XML ({exc})", []
    tags = {el.tag.split("}")[-1] for el in root.iter()}
    for t in _FORBIDDEN:
        if t in tags:
            return f"contains <{t}> (not vector truth)", []
    if _EXTERNAL.search(svg):
        return "references an external resource", []
    vb = [float(v) for v in re.findall(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?", root.get("viewBox", ""))]
    if len(vb) != 4 or vb[2] <= 0 or abs(vb[2] - vb[3]) > 1e-6:
        return f"viewBox is not square ({root.get('viewBox')!r})", []
    try:
        png = resvg_py.svg_to_bytes(svg_string=svg, width=SIZE, height=SIZE, skip_system_fonts=True)
        rgba = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))
    except Exception as exc:  # noqa: BLE001 - resvg raises plain exceptions
        return f"resvg cannot render it ({type(exc).__name__}: {exc})", []
    if rgba.shape[:2] != (SIZE, SIZE):
        return f"resvg rendered {rgba.shape[1]}x{rgba.shape[0]}, not {SIZE}x{SIZE}", []
    if (rgba[..., 3] > 0).mean() < 0.01:
        return "renders blank", []
    features = [f"has:{name}" for name, els in _FEATURES.items() if any(e in tags for e in els)]
    if re.search(r'(?<![\w-])(?:opacity|fill-opacity|stroke-opacity)\s*[=:]\s*"?\s*0?\.\d', svg):
        features.append("has:opacity")
    if "stroke" in svg and re.search(r'stroke\s*[=:]\s*"?\s*(?!none)[#a-zA-Z]', svg):
        features.append("has:stroke")
    return None, features


# ---------------------------------------------------------------- rasters

def build(root: Path = HERE) -> list:
    """Rasterise every SVG listed in sources.json and write manifest.yaml."""
    from bench.corpus import Item, write_manifest
    from bench.synth import render_downsampled, render_png

    sources = json.loads((root / "sources.json").read_text())
    items: list[Item] = []
    for s in sources["files"]:
        cls = s["set"]
        svg_path = root / s["truth_svg"]
        svg = svg_path.read_text(encoding="utf-8")
        stem = svg_path.stem
        reason, features = check(svg)
        if reason:
            raise SystemExit(f"{svg_path}: {reason}")
        tags = ["heldout", f"source:{s['repo_key']}", f"size:{SIZE}", *features]
        clean = root / cls / f"{stem}-{SIZE}.png"
        clean.write_bytes(render_png(svg, SIZE))
        ds = root / cls / f"{stem}-{SIZE}-ds.png"
        ds.write_bytes(render_downsampled(svg, SIZE))
        q75 = root / cls / f"{stem}-{SIZE}-q75.jpg"
        rgba = Image.open(clean).convert("RGBA")
        flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba).convert("RGB")
        buf = io.BytesIO()
        flat.save(buf, "JPEG", quality=75)
        q75.write_bytes(buf.getvalue())
        for path, suffix, extra in ((clean, "", []), (ds, "-ds", ["degraded:downsample"]), (q75, "-q75", ["degraded:jpeg-q75"])):
            items.append(Item(id=f"{cls}/{stem}-{SIZE}{suffix}", cls=cls, png=path, width=SIZE, height=SIZE,
                              truth_svg=svg_path, tags=tags + extra))
    write_manifest(root, items)
    return items


# ---------------------------------------------------------------- attribution

LICENSE_FILES = {"MIT": "LICENSES/MIT-fluentui-emoji.txt", "Apache-2.0": "LICENSES/Apache-2.0.txt"}


def write_sources(root: Path = HERE) -> Path:
    """SOURCES.md: licence and attribution for every file, from sources.json and fetch_log.json."""
    sources = json.loads((root / "sources.json").read_text())
    log = json.loads((root / "fetch_log.json").read_text())
    L = ["# Held-out corpus: sources and licences", "",
         "Emoji artwork from two open-source projects, fetched unmodified by `fetch.py` (byte-for-byte; the SHA-256",
         "of each file is below) at the pinned commits listed here. The rasters beside each SVG (`-512.png`,",
         "`-512-ds.png`, `-512-q75.jpg`) are renders of it made by `fetch.py`, and are covered by the same licence",
         "as the SVG they are rendered from. None of these images was used while developing Vexel; they are kept",
         "out of `bench/corpus`.", "",
         "| set | project | commit | licence | licence text |", "|---|---|---|---|---|"]
    by_key = {"fluent": ("fluent-flat, fluent-color",), "noto": ("noto",)}
    for key, spec in sources["repos"].items():
        L.append(f"| {by_key.get(key, (key,))[0]} | {spec['project']} | `{spec['sha']}` | {spec['license']} "
                 f"({spec['copyright']}) | [`{LICENSE_FILES[spec['license']]}`]({LICENSE_FILES[spec['license']]}) |")
    L += ["", "Microsoft Fluent Emoji is released under the MIT License, Copyright (c) Microsoft Corporation.",
          "Google Noto Emoji's image resources are released under the Apache License, Version 2.0 (the project's",
          "fonts are under the SIL Open Font License; no font file is used here). The Noto files are",
          "redistributed unmodified; the rasters are derived renders.", "",
          "The licence texts in `LICENSES/` were not downloaded from the two projects (only the SVGs were): the",
          "MIT text is the standard one with the Fluent Emoji copyright line, and the Apache-2.0 text is the",
          "standard one, copied from a local copy. The full SHA-256 of every file is in `sources.json`.", "",
          f"Sampling: seed `{log['seed']}`, " + "; ".join(
              f"{k}: {v['kept']} kept of {v['tried']} downloaded from {v['candidates']} candidates"
              for k, v in log["sets"].items()) + ".",
          ("Skipped: none — every downloaded file passed the content rules (see `fetch.py`)." if not
           [x for x in log["skipped"] if x["downloaded"]] else "Skipped after download:"), ""]
    for x in log["skipped"]:
        if x["downloaded"]:
            L.append(f"- `{x['path']}` ({x['set']}): {x['reason']}")
    L += ["", "| file | set | depicts | upstream path | licence | SHA-256 |", "|---|---|---|---|---|---|"]
    for f in sources["files"]:
        spec = sources["repos"][f["repo_key"]]
        L.append(f"| `{f['truth_svg']}` | {f['set']} | {f['subject']} | [`{f['upstream_path']}`]({f['url']}) | "
                 f"{spec['license']} | `{f['sha256'][:16]}…` |")
    out = root / "SOURCES.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------- main

def fetch(root: Path, cache: Path | None) -> dict:
    listings = {key: list_tree(key, cache) for key in REPOS}
    log: dict = {"seed": SEED, "sets": {}, "skipped": [], "downloads": 0}
    files: list[dict] = []
    taken_subjects: set[str] = set()
    for set_id, (key, quota) in SETS.items():
        spec = REPOS[key]
        pool = candidates(set_id, listings[key])
        order = list(pool)
        random.Random(f"{SEED}:{set_id}").shuffle(order)
        kept = 0
        tried = 0
        for path in order:
            if kept >= quota:
                break
            subj = subject(set_id, path)
            if subj.lower() in taken_subjects:
                log["skipped"].append({"set": set_id, "path": path, "reason": "subject already taken by another set",
                                       "downloaded": False})
                continue
            tried += 1
            url = f"https://raw.githubusercontent.com/{spec['repo']}/{spec['sha']}/{urllib.parse.quote(path)}"
            raw = _get(url)
            log["downloads"] += 1
            time.sleep(0.2)
            svg = raw.decode("utf-8", errors="replace")
            reason, features = check(svg)
            if reason:
                log["skipped"].append({"set": set_id, "path": path, "reason": reason, "downloaded": True})
                continue
            name = slug(set_id, path)
            out = root / set_id / f"{name}.svg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
            taken_subjects.add(subj.lower())
            kept += 1
            files.append({"set": set_id, "repo_key": key, "subject": subj, "upstream_path": path, "url": url,
                          "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                          "truth_svg": out.relative_to(root).as_posix(), "features": features})
        log["sets"][set_id] = {"candidates": len(pool), "tried": tried, "kept": kept, "quota": quota}
        print(f"{set_id:<13} {len(pool):>5} candidates, {tried} downloaded, {kept} kept")
    sources = {"seed": SEED, "repos": {k: {kk: v[kk] for kk in ("repo", "sha", "license", "copyright", "project")}
                                       for k, v in REPOS.items()}, "files": files}
    (root / "sources.json").write_text(json.dumps(sources, indent=1) + "\n")
    (root / "fetch_log.json").write_text(json.dumps(log, indent=1) + "\n")
    return sources


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.heldout.fetch", description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true", help="only rebuild rasters + manifest from the SVGs on disk")
    ap.add_argument("--cache", help="directory to keep the two tree listings in (skips re-listing)")
    args = ap.parse_args(argv)
    if not args.build:
        fetch(HERE, Path(args.cache) if args.cache else None)
    items = build(HERE)
    write_sources(HERE)
    print(f"built {len(items)} items under {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
