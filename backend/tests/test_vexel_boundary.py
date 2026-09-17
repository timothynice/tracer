import io

import numpy as np
import resvg_py
from PIL import Image

from studi0trace.engines.vexel.boundary import contours, coverage_field
from studi0trace.engines.vexel.order import enclosure, paint_order, shape_mask


def donut_labels(size=48):
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - 24, yy - 24)
    labels = np.ones((size, size), np.int32)  # 1 = background
    labels[r < 18] = 2  # ring
    labels[r < 8] = 3  # hole
    labels[5:9, 5:9] = 4  # a separate square on the background
    return labels


def test_enclosure_tree_and_paint_order():
    labels = donut_labels()
    enc = enclosure(labels)
    assert enc.parent == {1: None, 2: 1, 3: 2, 4: 1}
    assert enc.children[1] == [2, 4]  # larger child first
    assert paint_order(enc) == [1, 2, 3, 4]


def test_stacked_masks_include_descendants():
    labels = donut_labels()
    enc = enclosure(labels)
    ring = shape_mask(labels, 2, enc, stacked=True)
    assert ring[24, 24], "stacked ring covers its hole so the hole paints over it"
    assert not shape_mask(labels, 2, enc, stacked=False)[24, 24]
    assert shape_mask(labels, 1, enc, stacked=True).all()
    assert not shape_mask(labels, 4, enc, stacked=True)[24, 24]


def render_circle(size=64, r=20.3, cx=31.7, cy=32.4):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/><circle cx="{cx}" cy="{cy}" r="{r}" fill="#000"/></svg>'
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))


def test_subpixel_contour_beats_pixel_grid():
    img = render_circle()
    rgb = img[..., :3].astype(np.float32)
    alpha = np.ones(img.shape[:2], np.float32)
    inside = rgb[..., 0] < 128
    labels = np.where(inside, 2, 1).astype(np.int32)

    def fill_at(label, xs, ys):
        c = np.array([0.0, 0.0, 0.0, 255.0]) if label == 2 else np.array([255.0, 255.0, 255.0, 255.0])
        return np.tile(c, (xs.size, 1))

    field = coverage_field(labels == 2, 2, labels, rgb, alpha, fill_at)
    assert field[32, 32] == 1.0 and field[0, 0] == 0.0
    ring_vals = field[(rgb[..., 0] > 5) & (rgb[..., 0] < 250)]
    assert 0.0 < ring_vals.min() and ring_vals.max() < 1.0

    (poly,) = contours(field)
    radii = np.hypot(poly[:, 0] - 31.7, poly[:, 1] - 32.4)
    assert abs(radii.mean() - 20.3) < 0.1
    assert radii.std() < 0.12

    # the plain pixel grid (no anti-aliasing information) is visibly worse
    (poly_bin,) = contours(labels.astype(np.float32) == 2)
    assert np.hypot(poly_bin[:, 0] - 31.7, poly_bin[:, 1] - 32.4).std() > radii.std() * 1.5


def test_contours_handle_shapes_touching_the_border():
    field = np.zeros((10, 10), np.float32)
    field[:, :5] = 1.0
    (poly,) = contours(field)
    x, y = poly[:, 0], poly[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    assert abs(area - 50) < 1.0
    assert x.min() >= 0.0 - 1e-6 and x.max() <= 5.0 + 1e-6
