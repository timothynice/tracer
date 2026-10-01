//! Corners, rectangles and area (`quality.CornerInfo`, `_corners`, `_rect_like`, `_area`).
use super::accelerate::ddot_stride2;
use super::outline::turns;
use super::{
    degrees, py_round, radians, CORNER_MAX_DEG, CORNER_MIN_DEG, CORNER_SEED_DEG, CORNER_WINDOW, CURVED, GROW_MAX,
    RECT_GRID, RECT_SIDE_DEG, ROUND_R, SHARP_R, STEP,
};
use crate::drawing::path::cos_sin;
use crate::edges::pairwise_sum;

/// `quality.CornerInfo`: one convex corner of a closed outline.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct CornerInfo {
    /// The sample after its middle.
    pub at: [f64; 2],
    /// Sample index of the corner's middle.
    pub index: usize,
    /// First and one-past-last sample of its curved span (may wrap).
    pub lo: usize,
    pub hi: usize,
    pub turn_deg: f64,
    pub radius: f64,
}

/// `i mod n` for a possibly negative `i`, as Python's `%`.
fn wrap_index(i: i64, n: usize) -> usize {
    i.rem_euclid(n as i64) as usize
}

/// `_corners`: the convex corners of a closed outline that turn [`CORNER_MIN_DEG`] to
/// [`CORNER_MAX_DEG`], each with the radius it is drawn at. Seeded where [`CORNER_WINDOW`] px
/// turn [`CORNER_SEED_DEG`], strongest first, and grown over the curve it sits on up to
/// [`GROW_MAX`] px. `net_sign` is the sign of the outline's total turning.
pub(super) fn corners(q: &[[f64; 2]], net_sign: f64) -> Result<Vec<CornerInfo>, super::CardError> {
    if q.len() < 16 {
        return Ok(Vec::new());
    }
    let d = turns(q, true, 1); // d[i]: the turn at sample i + 1
    let n = d.len();
    let w = (py_round(CORNER_WINDOW / STEP)? as usize).min(n - 1);
    let h = w / 2;
    let ext = d[n - h..].iter().chain(&d).chain(&d[..w - h + 1]);
    let mut ce = vec![0.0];
    for (i, &v) in ext.enumerate() {
        ce.push(if i == 0 { v } else { ce[i] + v });
    }
    let conv: Vec<f64> = (0..n).map(|i| (ce[i + w] - ce[i]) * net_sign).collect();
    let signed: Vec<f64> = d.iter().map(|&v| v * net_sign).collect();
    let curved_all: Vec<bool> = signed.iter().map(|&v| v > CURVED * STEP).collect();
    let grow = ((py_round(GROW_MAX / STEP)?) as usize).min(n - 1) as i64;
    let (seed, lo_rad, hi_rad) = (radians(CORNER_SEED_DEG), radians(CORNER_MIN_DEG), radians(CORNER_MAX_DEG));
    // np.argsort(-conv, kind="stable"): descending, ties in index order, NaN last
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&a, &b| {
        let (x, y) = (-conv[a], -conv[b]);
        match (x.is_nan(), y.is_nan()) {
            (false, false) => x.partial_cmp(&y).unwrap(),
            (a, b) => a.cmp(&b),
        }
    });
    let mut taken = vec![false; n];
    let mut tried = vec![false; n]; // samples of a curve already grown from a seed and turned down
    let mut out = Vec::new();
    for i in order {
        if conv[i] < seed {
            break;
        }
        let start = i as i64 - h as i64;
        let window: Vec<usize> = (start..start + w as i64).map(|j| wrap_index(j, n)).collect();
        if tried[i] || window.iter().any(|&j| taken[j]) {
            continue;
        }
        let curved_at: Vec<usize> = (0..w).filter(|&j| curved_all[window[j]]).collect();
        let (Some(&first), Some(&last)) = (curved_at.first(), curved_at.last()) else { continue };
        let (mut a, mut b) = (start + first as i64, start + last as i64);
        while b - a < grow && curved_all[wrap_index(a - 1, n)] && !taken[wrap_index(a - 1, n)] {
            a -= 1;
        }
        while b - a < grow && curved_all[wrap_index(b + 1, n)] && !taken[wrap_index(b + 1, n)] {
            b += 1;
        }
        let span: Vec<usize> = (a..=b).map(|j| wrap_index(j, n)).collect();
        let turning: Vec<f64> = span.iter().filter(|&&j| curved_all[j]).map(|&j| signed[j]).collect();
        let turn = pairwise_sum(&turning);
        if !(lo_rad <= turn && turn <= hi_rad) {
            for &j in &span {
                tried[j] = true;
            }
            continue;
        }
        for j in a.min(start)..(b + 1).max(start + w as i64) {
            taken[wrap_index(j, n)] = true;
        }
        // The length over which it turns. A sharp vertex falls between two resampled points
        // and turns in one or two samples; a chamfer turns in two separated spikes and reads as
        // sharp, which is how it looks.
        let length = turning.len() as f64 * STEP;
        let radius = py_max(0.0, length - 2.0 * STEP) / turn;
        let mid = wrap_index((a + b).div_euclid(2), n);
        out.push(CornerInfo {
            at: q[(mid + 1) % n],
            index: mid,
            lo: wrap_index(a, n),
            hi: wrap_index(b + 1, n),
            turn_deg: degrees(turn),
            radius,
        });
    }
    out.sort_by_key(|c| c.index);
    Ok(out)
}

/// Python's `max(a, b)` of two floats: `b` only when it is greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `max(list)` of Python floats, the first greatest.
fn py_max_of(v: impl IntoIterator<Item = f64>) -> Option<f64> {
    v.into_iter().reduce(py_max)
}

/// `min(list)`, the first least.
fn py_min_of(v: impl IntoIterator<Item = f64>) -> Option<f64> {
    v.into_iter().reduce(|a, b| if b < a { b } else { a })
}

/// What `_rect_like` returns for a (rounded) rectangle.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct RectLike {
    pub radii: Vec<f64>,
    pub spread: f64,
    pub mixed: bool,
    pub bow: f64,
    pub skew: f64,
}

/// The direction of a side's best line: the first right singular vector of its centred points,
/// which numpy has LAPACK (`dgesdd`) compute. For two columns that is the principal axis of the
/// 2×2 scatter, `θ = atan2(2·sxy, sxx − syy) / 2`; it agrees with LAPACK to about an ulp of the
/// direction (1e-15 on 20 000 random sides), not to the bit. LAPACK's sign is its own; the one
/// taken here (the first point projecting negatively, which LAPACK gives 98.5% of the time on
/// those sides) changes a side's angle by 180° and the skew, which is taken modulo 90°, by
/// rounding only.
fn principal_direction(centred: &[[f64; 2]]) -> [f64; 2] {
    let (mut sxx, mut sxy, mut syy) = (0.0, 0.0, 0.0);
    for p in centred {
        sxx += p[0] * p[0];
        sxy += p[0] * p[1];
        syy += p[1] * p[1];
    }
    let theta = 0.5 * (2.0 * sxy).atan2(sxx - syy);
    let (c, s) = cos_sin(theta);
    let first = centred[0];
    if first[0] * c + first[1] * s > 0.0 {
        [-c, -s]
    } else {
        [c, s]
    }
}

/// `_rect_like`: for an outline with exactly four corners whose sides each turn less than
/// [`RECT_SIDE_DEG`] and sit on a right-angle grid within [`RECT_GRID`], its corners' radii and
/// how far their spread, the sides' bow off their own lines and their skew; `None` for anything
/// else. With `visible`, only the corners and the stretches of side on screen are judged.
pub(super) fn rect_like(q: &[[f64; 2]], corners: &[CornerInfo], visible: Option<&[bool]>) -> Option<RectLike> {
    if corners.len() != 4 {
        return None;
    }
    let n = q.len();
    let d = turns(q, true, 1);
    let (mut bows, mut angles) = (Vec::with_capacity(4), Vec::with_capacity(4));
    for (i, a) in corners.iter().enumerate() {
        let b = &corners[(i + 1) % 4];
        let lo = (a.hi + 1) % n;
        let m = wrap_index(b.lo as i64 - lo as i64, n);
        if (m as f64) * STEP < 2.0 {
            return None;
        }
        let at: Vec<usize> = (0..=m).map(|j| (lo + j) % n).collect();
        let side_turn: Vec<f64> = (0..m).map(|j| d[(lo + j) % n]).collect();
        if degrees(pairwise_sum(&side_turn)).abs() > RECT_SIDE_DEG {
            return None;
        }
        // side.mean(axis=0): numpy adds the rows of an (m, 2) array in order
        let (mut sx, mut sy) = (0.0, 0.0);
        for (k, &j) in at.iter().enumerate() {
            sx = if k == 0 { q[j][0] } else { sx + q[j][0] };
            sy = if k == 0 { q[j][1] } else { sy + q[j][1] };
        }
        let c0 = [sx / at.len() as f64, sy / at.len() as f64];
        let centred: Vec<[f64; 2]> = at.iter().map(|&j| [q[j][0] - c0[0], q[j][1] - c0[1]]).collect();
        let direction = principal_direction(&centred);
        let normal = [-direction[1], direction[0]];
        let dev = centred
            .iter()
            .zip(&at)
            .filter(|(_, &j)| visible.is_none_or(|v| v[j]))
            .map(|(p, _)| (p[0] * normal[0] + p[1] * normal[1]).abs());
        // np.max, which a NaN wins
        bows.push(dev.reduce(|x, y| if x.is_nan() || y > x { y } else { x }).unwrap_or(0.0));
        angles.push(degrees(direction[1].atan2(direction[0])));
    }
    let reference = angles[0];
    let skew = py_max_of(angles.iter().map(|ang| (py_mod(ang - reference + 45.0, 90.0) - 45.0).abs())).unwrap();
    if skew > RECT_GRID {
        return None; // a trapezoid, a rhombus: drawn that way on purpose
    }
    let shown: Vec<f64> = corners.iter().filter(|c| visible.is_none_or(|v| v[c.index % n])).map(|c| c.radius).collect();
    let (max, min) = (py_max_of(shown.iter().copied()), py_min_of(shown.iter().copied()));
    let (spread, mixed) = match (max, min) {
        (Some(hi), Some(lo)) if shown.len() >= 2 => (hi - lo, hi >= ROUND_R && lo < SHARP_R),
        _ => (0.0, false),
    };
    Some(RectLike {
        radii: corners.iter().map(|c| c.radius).collect(),
        spread,
        mixed,
        bow: py_max_of(bows).unwrap(),
        skew,
    })
}

/// Python's float `%`: the remainder with the sign of the divisor.
fn py_mod(a: f64, b: f64) -> f64 {
    let m = a % b;
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m + b
        } else {
            m
        }
    } else {
        0.0f64.copysign(b)
    }
}

/// `_area`: the shoelace sum, as numpy dots `x` with `y` rolled by one (Accelerate's strided
/// `ddot`, so the products are fused and summed four ways).
pub(super) fn area(q: &[[f64; 2]]) -> f64 {
    let n = q.len();
    let xy = ddot_stride2(n, |i| q[i][0], |i| q[(i + 1) % n][1]);
    let yx = ddot_stride2(n, |i| q[i][1], |i| q[(i + 1) % n][0]);
    0.5 * (xy - yx)
}
