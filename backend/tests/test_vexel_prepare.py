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


def test_colour_under_faint_alpha_is_inpainted_from_the_nearest_pixel_that_shows():
    """A rasteriser that works premultiplied and unpremultiplies for the file
    leaves, under a pixel of alpha a, a colour quantised to steps of 255/a: at
    alpha 1 or 2 only 0, 128 and 255 per channel. That colour means nothing,
    like the colour under alpha 0, and is read from the nearest pixel whose
    alpha is high enough for its colour to be its own."""
    img = rgba(color=(0, 0, 0, 0))
    img[8:24, 8:24] = (200, 50, 50, 255)  # red square
    img[24:28, 8:24] = (255, 0, 255, 2)  # a faint halo below it, magenta noise (on the 255/2 grid)
    img[28:30, 8:24] = (255, 255, 255, 1)  # and fainter, white (the only values alpha 1 can hold)
    img[8:24, 24:26] = (255, 255, 255, 40)  # a real anti-aliased rim: its colour is its own
    img[8:24, 4:6] = (120, 40, 200, 3)  # off the 255/3 grid: a straight-alpha file's own colour
    p = prepare(img)
    assert tuple(p.rgb[26, 16]) == (200, 50, 50)
    assert tuple(p.rgb[29, 16]) == (200, 50, 50)
    assert tuple(p.rgb[16, 24]) == (255, 255, 255)
    assert tuple(p.rgb[16, 4]) == (120, 40, 200), "a colour no unpremultiply could have made is kept"
    assert np.isclose(p.alpha[26, 16], 2 / 255) and np.isclose(p.alpha[29, 16], 1 / 255)
    assert np.isclose(p.features[26, 16, 3], 100 * 2 / 255, atol=1e-3)
    # the grid at alpha 3 is 0, 85, 170, 255: a level of rounding either way still counts
    img[8:24, 4:6] = (86, 169, 0, 3)
    assert tuple(prepare(img).rgb[16, 4]) == (200, 50, 50)
