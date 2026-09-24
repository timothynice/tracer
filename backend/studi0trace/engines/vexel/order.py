"""Stage 5: enclosure tree and painter's order.

A region B is *enclosed* by A when B does not touch the image border and the
only label adjacent to the outside of B's hole-filled footprint is A. Enclosed
regions paint after their parent; in stacked mode a region's shape includes
all of its descendants so children cover it seamlessly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

_CROSS = ndimage.generate_binary_structure(2, 1)


@dataclass
class Enclosure:
    parent: dict[int, int | None]
    children: dict[int, list[int]] = field(default_factory=dict)
    area: dict[int, int] = field(default_factory=dict)
    border: dict[int, int] = field(default_factory=dict)

    def descendants(self, label: int) -> list[int]:
        out: list[int] = []
        stack = list(self.children.get(label, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children.get(c, []))
        return out


def enclosure(labels: np.ndarray) -> Enclosure:
    ids = [int(i) for i in np.unique(labels) if i != 0]
    area = {int(i): int(a) for i, a in zip(*np.unique(labels, return_counts=True)) if i != 0}
    border_mask = np.zeros(labels.shape, bool)
    border_mask[0, :] = border_mask[-1, :] = border_mask[:, 0] = border_mask[:, -1] = True
    border = {i: int(np.count_nonzero(labels[border_mask] == i)) for i in ids}

    parent: dict[int, int | None] = {}
    for i in ids:
        if border[i] > 0:
            parent[i] = None
            continue
        mask = labels == i
        filled = ndimage.binary_fill_holes(mask)
        ring = ndimage.binary_dilation(filled, _CROSS) & ~filled
        outer = np.unique(labels[ring])
        outer = outer[(outer != 0) & (outer != i)]
        parent[i] = int(outer[0]) if outer.size == 1 else None

    children: dict[int, list[int]] = {i: [] for i in ids}
    for i, p in parent.items():
        if p is not None:
            children[p].append(i)
    for p in children:
        children[p].sort(key=lambda c: -area[c])
    return Enclosure(parent=parent, children=children, area=area, border=border)


def paint_order(enc: Enclosure) -> list[int]:
    """Background-most root first, then depth-first by enclosure, siblings largest first."""
    roots = [i for i, p in enc.parent.items() if p is None]
    # ties go to the lower label, in both engines, never to an iteration order
    roots.sort(key=lambda i: (-enc.border[i], -enc.area[i], i))
    out: list[int] = []

    def visit(i: int) -> None:
        out.append(i)
        for c in enc.children.get(i, []):
            visit(c)

    for r in roots:
        visit(r)
    return out


def shape_labels(label: int, enc: Enclosure, stacked: bool, invisible: set[int] | None = None) -> frozenset[int]:
    """The labels this element paints over: its own, plus (when stacked) every
    descendant painted on top of it. Invisible descendants — transparent holes —
    are left out, or the hole would vanish under its parent."""
    invisible = invisible or set()
    out = {label}
    if not stacked:
        return frozenset(out)
    stack = [c for c in enc.children.get(label, []) if c not in invisible]
    while stack:
        c = stack.pop()
        out.add(c)
        stack.extend(g for g in enc.children.get(c, []) if g not in invisible)
    return frozenset(out)


def shape_mask(labels: np.ndarray, label: int, enc: Enclosure, stacked: bool, invisible: set[int] | None = None) -> np.ndarray:
    """Pixels this element paints: its own, plus (when stacked) every descendant
    that will be painted on top. Invisible descendants — transparent holes — are
    never covered, or the hole would disappear under the parent."""
    mask = labels == label
    if stacked:
        invisible = invisible or set()
        stack = [c for c in enc.children.get(label, []) if c not in invisible]
        while stack:
            c = stack.pop()
            mask |= labels == c
            stack.extend(g for g in enc.children.get(c, []) if g not in invisible)
    return mask
