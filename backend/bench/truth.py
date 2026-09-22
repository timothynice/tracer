"""Corners a vector truth says the artwork has.

A corner is a point where the outline's tangent turns: a polygon vertex, a
square rect's corner, a path's join between two straight or curved pieces
whose directions differ. Circles, ellipses, rounded rects and tangent arc
joins have none. Shapes under a blur filter are soft and yield none. This is
the reference `corner_f1` in `bench.geometry` compares the trace against —
where the artwork has a corner, the trace should have one within a pixel,
and nowhere else.
"""
from __future__ import annotations

import math
import re

import numpy as np

_NUM = re.compile(r"-?\d*\.?\d+(?:e[-+]?\d+)?")
CORNER_TURN_DEG = 20.0  # a join turning less than this is a smooth join, not a corner


def _nums(s: str) -> list[float]:
    return [float(v) for v in _NUM.findall(s)]


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def _path_corners(d: str) -> list[tuple[float, float]]:
    """Joins of an M/L/H/V/C/S/Q/A/Z path (absolute or relative) where the
    tangent turns by more than CORNER_TURN_DEG."""
    pieces: list[list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]] = []  # per subpath: (start, in_dir, out_dir) per segment
    cur = start = (0.0, 0.0)
    segs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []  # (p0, t_in, t_out, p1)
    for cmd, body in re.findall(r"([MLHVCSQAZmlhvcsqaz])([^MLHVCSQAZmlhvcsqaz]*)", d):
        v = _nums(body)
        rel = cmd.islower()
        c = cmd.upper()

        def pt(x: float, y: float) -> tuple[float, float]:
            return (cur[0] + x, cur[1] + y) if rel else (x, y)

        if c == "M":
            if segs:
                pieces.append(segs)
                segs = []
            cur = start = pt(v[0], v[1])
            for k in range(2, len(v) - 1, 2):
                p = pt(v[k], v[k + 1])
                segs.append((cur, (p[0] - cur[0], p[1] - cur[1]), (p[0] - cur[0], p[1] - cur[1]), p))
                cur = p
        elif c in "LHV":
            k = 0
            while k < len(v):
                if c == "L":
                    p = pt(v[k], v[k + 1]); k += 2
                elif c == "H":
                    p = ((cur[0] + v[k]) if rel else v[k], cur[1]); k += 1
                else:
                    p = (cur[0], (cur[1] + v[k]) if rel else v[k]); k += 1
                t = (p[0] - cur[0], p[1] - cur[1])
                segs.append((cur, t, t, p))
                cur = p
        elif c == "C":
            for k in range(0, len(v) - 5, 6):
                c1, c2, p = pt(v[k], v[k + 1]), pt(v[k + 2], v[k + 3]), pt(v[k + 4], v[k + 5])
                segs.append((cur, (c1[0] - cur[0], c1[1] - cur[1]), (p[0] - c2[0], p[1] - c2[1]), p))
                cur = p
        elif c == "A":
            for k in range(0, len(v) - 6, 7):
                p = pt(v[k + 5], v[k + 6])
                rx, large, sweep = v[k], v[k + 3] != 0, v[k + 4] != 0
                # tangents of a circular arc at its ends (equal radii)
                mid = ((cur[0] + p[0]) / 2, (cur[1] + p[1]) / 2)
                dx, dy = p[0] - cur[0], p[1] - cur[1]
                half = math.hypot(dx, dy) / 2
                r = max(rx, half)
                h = math.sqrt(max(r * r - half * half, 0.0))
                nx, ny = (-dy / (2 * half), dx / (2 * half)) if half > 1e-12 else (0.0, 0.0)
                cx, cy = (mid[0] + nx * h, mid[1] + ny * h) if sweep != large else (mid[0] - nx * h, mid[1] - ny * h)
                def tangent(q: tuple[float, float]) -> tuple[float, float]:
                    ux, uy = q[0] - cx, q[1] - cy
                    return (-uy, ux) if sweep else (uy, -ux)
                segs.append((cur, tangent(cur), tangent(p), p))
                cur = p
        elif c in "SQ":
            step = 4
            for k in range(0, len(v) - step + 1, step):
                cq, p = pt(v[k], v[k + 1]), pt(v[k + 2], v[k + 3])
                segs.append((cur, (cq[0] - cur[0], cq[1] - cur[1]), (p[0] - cq[0], p[1] - cq[1]), p))
                cur = p
        elif c == "Z":
            if cur != start:
                t = (start[0] - cur[0], start[1] - cur[1])
                segs.append((cur, t, t, start))
            cur = start
            if segs:
                pieces.append(segs)
                segs = []
    if segs:
        pieces.append(segs)
    out: list[tuple[float, float]] = []
    for piece in pieces:
        n = len(piece)
        for k in range(n):
            a, b = piece[k - 1], piece[k]
            if k == 0 and (abs(a[3][0] - b[0][0]) > 1e-6 or abs(a[3][1] - b[0][1]) > 1e-6):
                continue  # open subpath: its ends are not joins
            tin, tout = a[2], b[1]
            n1, n2 = math.hypot(*tin), math.hypot(*tout)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            cosang = max(-1.0, min(1.0, (tin[0] * tout[0] + tin[1] * tout[1]) / (n1 * n2)))
            if math.degrees(math.acos(cosang)) > CORNER_TURN_DEG:
                out.append(b[0])
    return out


def corners(svg: str) -> np.ndarray:
    """(N, 2) corner positions of the visible artwork, in user units."""
    out: list[tuple[float, float]] = []
    for tag in re.findall(r"<(?:polygon|rect|path)\b[^>]*>", svg):
        if "filter=" in tag or 'fill="none"' in tag:
            continue
        if tag.startswith("<polygon"):
            v = _nums(_attr(tag, "points") or "")
            pts = [(v[k], v[k + 1]) for k in range(0, len(v) - 1, 2)]
            n = len(pts)
            for k in range(n):
                a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
                t1, t2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
                n1, n2 = math.hypot(*t1), math.hypot(*t2)
                if n1 > 1e-9 and n2 > 1e-9:
                    cosang = max(-1.0, min(1.0, (t1[0] * t2[0] + t1[1] * t2[1]) / (n1 * n2)))
                    if math.degrees(math.acos(cosang)) > CORNER_TURN_DEG:
                        out.append(b)
        elif tag.startswith("<rect"):
            if float(_attr(tag, "rx") or 0) > 0 or float(_attr(tag, "ry") or 0) > 0:
                continue
            x, y = float(_attr(tag, "x") or 0), float(_attr(tag, "y") or 0)
            w, h = float(_attr(tag, "width") or 0), float(_attr(tag, "height") or 0)
            out.extend([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        else:
            out.extend(_path_corners(_attr(tag, "d") or ""))
    return np.array(out, dtype=float).reshape(-1, 2)


def emitted_corners(svg: str) -> np.ndarray:
    """(N, 2) corners of a traced SVG: its paths' turning joins, its square
    rects' corners, and the same for every `<use>` of a definition, moved."""
    defs: dict[str, str] = {}
    for tag in re.findall(r"<(?:rect|path|circle|ellipse)\b[^>]*\bid=\"[^\"]*\"[^>]*>", svg):
        defs[_attr(tag, "id") or ""] = tag
    out: list[tuple[float, float]] = []

    def of(tag: str) -> list[tuple[float, float]]:
        if tag.startswith("<rect"):
            if float(_attr(tag, "rx") or 0) > 0:
                return []
            x, y = float(_attr(tag, "x") or 0), float(_attr(tag, "y") or 0)
            w, h = float(_attr(tag, "width") or 0), float(_attr(tag, "height") or 0)
            return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        if tag.startswith("<path"):
            return _path_corners(_attr(tag, "d") or "")
        return []

    body = re.sub(r"<defs>.*?</defs>", "", svg, flags=re.S)
    for tag in re.findall(r"<(?:rect|path|use)\b[^>]*>", body):
        if 'fill="none"' in tag:
            continue  # a stroke's centreline has no area corners
        if tag.startswith("<use"):
            ref = (_attr(tag, "href") or "").lstrip("#")
            dx, dy = float(_attr(tag, "x") or 0), float(_attr(tag, "y") or 0)
            out.extend((x + dx, y + dy) for x, y in of(defs.get(ref, "")))
        else:
            out.extend(of(tag))
    return np.array(out, dtype=float).reshape(-1, 2)


def corner_match(truth_svg: str, out_svg: str, tol: float = 1.0) -> dict:
    """Precision, recall and F1 of the trace's corners against the truth's,
    a corner counted found when one lies within `tol` px of it."""
    from scipy.spatial import cKDTree

    want, got = corners(truth_svg), emitted_corners(out_svg)
    if len(want) == 0 and len(got) == 0:
        return {"corner_precision": 1.0, "corner_recall": 1.0, "corner_f1": 1.0}
    if len(want) == 0 or len(got) == 0:
        return {"corner_precision": 0.0 if len(got) else 1.0, "corner_recall": 0.0 if len(want) else 1.0, "corner_f1": 0.0}
    recall = float((cKDTree(got).query(want)[0] <= tol).mean())
    precision = float((cKDTree(want).query(got)[0] <= tol).mean())
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return {"corner_precision": precision, "corner_recall": recall, "corner_f1": f1}
