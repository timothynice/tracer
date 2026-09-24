"""The runtime scorecard scores what is on screen, and reads rounded rectangles steadily."""
from __future__ import annotations

import numpy as np
import pytest

from studi0trace.imaging import quality as Q

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
BACK = '<path d="M0 0H64V64H0Z" fill="#ffffff"/>'
WHITE = np.full((64, 64, 4), 255, np.uint8)

# A square whose right edge has a nick at x = 40, and a later square laid over
# that edge: the nick is part of the first square's path and never on screen.
NICKED = '<path d="M8 8H40V30L41.2 31L40 32V56H8Z" fill="#0af"/>'
S_EDGE = '<path d="M8 8H40C38.8 24 41.2 40 40 56H8Z" fill="#0af"/>'  # right edge drawn as a shallow S
OVER = '<path d="M36 4H60V60H36Z" fill="#e33"/>'


def card(body: str, **kw) -> dict:
    return Q.scorecard(HEAD + body + "</svg>", WHITE, detail=True, **kw)


def test_wobble_under_a_later_shape_is_not_scored():
    hidden = card(BACK + NICKED + OVER)
    assert hidden["wobble_deg_100px"] < 1.0
    # the same geometry scored blind to what covers it: the old reading
    assert card(BACK + NICKED + OVER, visibility=False)["wobble_deg_100px"] > 10.0
    assert hidden["hidden_len_px"] > 40.0


def test_wobble_on_screen_is_still_scored():
    # the same nick with nothing over it, and with a shape over the other side of the square
    assert card(BACK + NICKED)["wobble_deg_100px"] > 10.0
    left = '<path d="M4 4H20V60H4Z" fill="#e33"/>'
    assert card(BACK + NICKED + left)["wobble_deg_100px"] > 10.0


def test_a_translucent_shape_hides_nothing():
    veil = '<path d="M36 4H60V60H36Z" fill="#e33" fill-opacity="0.3"/>'
    assert card(BACK + NICKED + veil)["wobble_deg_100px"] > 10.0


def test_inflection_under_a_later_shape_is_not_scored():
    assert card(BACK + S_EDGE)["inflections"] >= 1
    assert card(BACK + S_EDGE + OVER)["inflections"] == 0


def test_sliver_under_a_later_shape_is_not_scored_but_degenerate_is():
    body = (BACK + '<path d="M40 20H50V20.4H40Z" fill="#123"/>'   # hairline sliver
            '<path d="M44 40L46 42L44 40Z" fill="#123"/>')          # zero-area subpath
    assert card(body)["slivers"] == 1
    covered = card(body + OVER)
    assert covered["slivers"] == 0
    assert covered["degenerate"] == 1  # junk in the file wherever it lies


def test_hidden_corner_does_not_make_a_rect_inconsistent():
    d = "M16 12H48A4 4 0 0 1 52 16V52H16A4 4 0 0 1 12 48V16A4 4 0 0 1 16 12Z"  # bottom-right corner sharp
    assert card(BACK + f'<path d="{d}" fill="#0af"/>')["radius_inconsistent"] == 1
    over = '<path d="M46 46H62V62H46Z" fill="#e33"/>'
    c = card(BACK + f'<path d="{d}" fill="#0af"/>' + over)
    assert c["radius_inconsistent"] == 0


def test_bled_copy_between_tiling_shapes_is_hidden():
    """What the engine does: the earlier shape runs a pixel under the later one."""
    body = BACK + '<path d="M8 8H33V56H8Z" fill="#0af"/><path d="M32 8H56V56H32Z" fill="#e33"/>'
    d = Q.parse(HEAD + body + "</svg>", (64, 64))
    ids = Q.id_map(d, (64, 64))
    first = next(c for c in d.contours if c.element == 1)
    q = Q._resample(first.pts, True)
    vis = Q.visible_samples(q, True, 1, None, ids, Q.ID_SCALE)
    right = np.abs(q[:, 0] - 33.0) < 1e-6
    inner = right & (q[:, 1] > 10) & (q[:, 1] < 54)
    assert not vis[inner].any(), "the copy under the later shape reads as on screen"
    assert vis[np.abs(q[:, 0] - 8.0) < 1e-6].all()


def test_id_map_paints_each_element_and_each_use_its_own_id():
    svg = (HEAD + '<defs><path id="u" d="M0 0H8V8H0Z"/></defs>' + BACK
           + '<use href="#u" x="4" y="4" fill="#000"/><use href="#u" x="40" y="40" fill="#000"/>'
           '<path d="M20 20H30V30H20Z" fill="#0af" opacity="0.2"/></svg>')
    d = Q.parse(svg, (64, 64))
    ids = Q.id_map(d, (64, 64), scale=2)
    assert ids[2 * 8, 2 * 8] == 1 and ids[2 * 44, 2 * 44] == 2
    assert ids[2 * 25, 2 * 25] == 0, "a shape at 20% opacity does not cover the backdrop"
    assert ids[2 * 60, 2 * 2] == 0


@pytest.mark.parametrize("seed", range(4))
def test_rounded_rect_primitives_read_as_clean_rects(seed):
    """A <rect rx> is the cleanest rounded rectangle there is. The corner search
    used to stop at a 10 px window, so a radius over about 7.5 px left part of
    each quarter circle on the sides: the rect read as bowed (38.5x40.2 at
    rx 8.16 bowed 0.33 px) or was not recognised at all."""
    rng = np.random.default_rng(seed)
    src = np.full((128, 128, 4), 255, np.uint8)
    for _ in range(12):
        w, h = rng.uniform(20, 90, 2)
        x, y = rng.uniform(2, 126 - max(w, h), 2)
        rx = rng.uniform(1, min(12.0, (min(w, h) - 6.0) / 2))  # sides at least 6 px straight: a rect, not a pill
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect x="{x:.2f}" y="{y:.2f}" '
               f'width="{w:.2f}" height="{h:.2f}" rx="{rx:.2f}" fill="#0af"/></svg>')
        c = Q.scorecard(svg, src, detail=True)
        assert c["rect_like"] == 1, (w, h, rx)
        assert c["rect_bowed"] == 0 and c["radius_inconsistent"] == 0 and c["rect_skewed"] == 0, (w, h, rx, c["_radius_at"])


def test_the_rect_that_read_as_bowed():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect x="10" y="10" width="38.5" '
           'height="40.2" rx="8.16" fill="#0af"/></svg>')
    d = Q.parse(svg, (128, 128))
    q = Q._resample(d.contours[0].pts, True)
    corners = Q._corners(q, 1.0 if Q._turns(q, True, 1).sum() >= 0 else -1.0)
    assert len(corners) == 4
    assert all(abs(c.turn_deg - 90.0) < 1.0 for c in corners)
    assert all(abs(c.radius - 8.16) < 0.4 for c in corners)
    rect = Q._rect_like(q, corners)
    assert rect is not None and rect["bow"] < 0.05


def test_scoring_blind_to_visibility_matches_the_whole_outline_total():
    """With nothing hidden, the per-sample form of the wobble is the old total."""
    body = BACK + NICKED + '<path d="M44 10C50 14 54 12 58 20" fill="none" stroke="#000" stroke-width="2"/>'
    a = card(body)
    b = card(body, visibility=False)
    assert a["wobble_deg_100px"] == pytest.approx(b["wobble_deg_100px"], rel=1e-9, abs=1e-9)


def test_assess_reports_fidelity_and_the_card():
    svg = HEAD + BACK + '<rect x="16" y="16" width="32" height="32" rx="4" fill="#000"/></svg>'
    src = Q.render(svg, 64, 64)
    out = Q.assess(svg, Q.Reference(src))
    assert out["delta_e_mean"] < 0.01 and out["edge_f1"] == pytest.approx(1.0)
    assert out["artifact_index"] < 1e-6 and Q.is_clean(out)
