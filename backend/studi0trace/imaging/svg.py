"""SVG normalisation and lightweight statistics.

Engines produce SVG with differing unit conventions (Potrace emits points,
VTracer pixels). `normalize_dimensions` strips width/height from the root
element and forces a viewBox matching the source raster so every engine's
output scales identically in the browser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ROOT_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_WIDTH = re.compile(r'\s+width="[^"]*"', re.IGNORECASE)
_HEIGHT = re.compile(r'\s+height="[^"]*"', re.IGNORECASE)
_VIEWBOX = re.compile(r'\s+viewBox="[^"]*"', re.IGNORECASE)

_PATH_TAG = re.compile(r"<path\b", re.IGNORECASE)
_D_ATTR = re.compile(r'\bd="([^"]*)"', re.IGNORECASE)
_PATH_CMD = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")
_GRADIENT = re.compile(r"<(?:linear|radial)Gradient\b", re.IGNORECASE)
_FILL_ATTR = re.compile(r'\bfill="([^"]*)"', re.IGNORECASE)
_FILL_STYLE = re.compile(r"fill\s*:\s*([^;\"']+)", re.IGNORECASE)


@dataclass(frozen=True)
class SvgStats:
    paths: int
    nodes: int
    bytes: int
    gradients: int
    unique_fills: int

    def as_dict(self) -> dict[str, int]:
        return {
            "paths": self.paths,
            "nodes": self.nodes,
            "bytes": self.bytes,
            "gradients": self.gradients,
            "unique_fills": self.unique_fills,
        }


def normalize_dimensions(svg: str, width: int, height: int) -> str:
    """Remove width/height from the root <svg> and set viewBox="0 0 W H".

    Only the root tag is touched; nested elements keep their attributes.
    Idempotent.
    """
    match = _ROOT_TAG.search(svg)
    if not match:
        return svg
    root = match.group(0)
    root = _WIDTH.sub("", root)
    root = _HEIGHT.sub("", root)
    root = _VIEWBOX.sub("", root)
    viewbox = f' viewBox="0 0 {width} {height}"'
    closing = ">" if not root.rstrip().endswith("/>") else "/>"
    root = root[: len(root) - len(closing)].rstrip() + viewbox + closing
    return svg[: match.start()] + root + svg[match.end():]


def svg_stats(svg: str) -> SvgStats:
    nodes = sum(len(_PATH_CMD.findall(d)) for d in _D_ATTR.findall(svg))
    fills = {f.strip().lower() for f in _FILL_ATTR.findall(svg)} | {
        f.strip().lower() for f in _FILL_STYLE.findall(svg)
    }
    fills.discard("none")
    fills.discard("")
    return SvgStats(
        paths=len(_PATH_TAG.findall(svg)),
        nodes=nodes,
        bytes=len(svg.encode("utf-8")),
        gradients=len(_GRADIENT.findall(svg)),
        unique_fills=len(fills),
    )
