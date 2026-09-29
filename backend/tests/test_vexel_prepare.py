import numpy as np

from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.prepare import prepare


def rgba(h=32, w=32, color=(255, 255, 255, 255)):
    img = np.zeros((h, w, 4), np.uint8)
    img[...] = color
    return img


def test_prepare_shapes_and_alpha():
    img = rgba(color=(10, 20, 30, 128))
    p = prepare(img)
    assert p.rgb.shape == (32, 32, 3) and p.alpha.shape == (32, 32) and p.features.shape == (32, 32, 4)
    assert np.isclose(p.alpha[0, 0], 128 / 255)
    assert np.isclose(p.features[0, 0, 3], 100 * 128 / 255, atol=0.1)
    assert p.width == 32 and p.height == 32


def test_transparent_pixels_inherit_nearest_visible_colour():
    img = rgba(color=(0, 0, 0, 0))  # transparent black canvas
    img[8:24, 8:24] = (200, 50, 50, 255)  # red square
    p = prepare(img)
    assert tuple(p.rgb[0, 0]) == (200, 50, 50), "corner should be inpainted from the square"
    assert p.alpha[0, 0] == 0.0
    # a fully transparent image is left alone
    assert prepare(rgba(color=(0, 0, 0, 0))).rgb.sum() == 0


def test_discontinuity_peaks_on_edges_only():
    img = rgba()
    img[:, 16:] = (0, 0, 0, 255)
    p = prepare(img)
    g = discontinuity(p.features, sigma=0)
    assert g[:, 14:18].max() > 50
    assert g[:, :10].max() < 1e-3 and g[:, 22:].max() < 1e-3


def test_two_flat_halves_give_two_regions():
    img = rgba()
    img[:, 16:] = (0, 0, 0, 255)
    p = prepare(img)
    labels = initial_labels(discontinuity(p.features), p.features, min_region=6)
    assert labels.min() == 1
    assert len(np.unique(labels)) == 2
    assert len(np.unique(labels[:, :12])) == 1 and len(np.unique(labels[:, 20:])) == 1
    assert labels[0, 0] != labels[0, 31]


def test_flat_image_is_one_region_and_gradient_does_not_split_on_edges():
    assert len(np.unique(initial_labels(discontinuity(prepare(rgba()).features), prepare(rgba()).features))) == 1

    img = rgba(64, 64)
    ramp = np.linspace(40, 200, 64).astype(np.uint8)
    img[..., 0] = ramp[None, :]
    img[..., 1] = ramp[None, :]
    img[..., 2] = ramp[None, :]
    img[16:48, 16:48] = (255, 0, 0, 255)  # a hard-edged square on the ramp
    p = prepare(img)
    labels = initial_labels(discontinuity(p.features), p.features)
    square = labels[20:44, 20:44]
    assert len(np.unique(square)) == 1
    assert labels[20, 20] not in np.unique(labels[:8, :])  # the square is not the ramp region


def test_inpainting_ignores_the_colour_of_nearly_transparent_pixels():
    """An 8-bit straight-alpha pixel's colour is stored to ±128/alpha levels: at
    alpha 2 it is noise. A downsampled asset rings every edge with such pixels,
    and inpainting the transparent field from them laid seams of that noise
    across it that the partition read as edges (thin-mark-512-ds's triangle
    came out serrated). The field, and the noise pixels themselves, take the
    colour of the nearest pixel whose alpha makes its colour trustworthy."""
    img = rgba(color=(0, 0, 0, 0))
    img[8:24, 8:24] = (200, 50, 50, 255)
    ys, xs = np.mgrid[0:32, 0:32]
    rim = ((xs == 7) | (xs == 24) | (ys == 7) | (ys == 24)) & (xs >= 7) & (xs <= 24) & (ys >= 7) & (ys <= 24)
    img[rim] = (255, 0, 255, 2)  # un-premultiplication garbage at alpha 2
    p = prepare(img)
    assert np.abs(p.rgb[0, 0] - (200, 50, 50)).max() < 2, "the transparent field is inpainted from the square, not the rim"
    assert np.abs(p.rgb[7, 0] - (200, 50, 50)).max() < 2, "even where the rim is the nearest visible pixel"
    assert tuple(p.rgb[7, 12]) == (255, 0, 255), "a visible pixel keeps the colour it has"
    assert p.alpha[7, 12] == np.float32(2 / 255)
    # a faint field keeps its own colour: a shadow's halo is black however
    # near the red caster, and the transparent canvas beyond it is black too
    halo = rgba(color=(0, 0, 0, 0))
    halo[8:24, 8:24] = (220, 40, 40, 255)
    halo[24:30, 8:24] = (0, 0, 0, 6)
    q = prepare(halo)
    assert np.abs(q.rgb[28, 16]).max() < 1, "the halo's own black stands"
    assert np.abs(q.rgb[31, 16]).max() < 1, "and the canvas beyond it is inpainted from the halo"
    # a genuinely translucent shape keeps its own colour: nothing above it to inpaint from
    soft = rgba(color=(0, 0, 0, 0))
    soft[8:24, 8:24] = (10, 20, 30, 40)
    assert tuple(prepare(soft).rgb[12, 12]) == (10, 20, 30)
    assert tuple(prepare(soft).rgb[0, 0]) == (10, 20, 30)
