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
