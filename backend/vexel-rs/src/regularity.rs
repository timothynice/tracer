//! Stage 7b: regularity across the boundary graph. Mirrors `regularity.py`.
//!
//! Every straight segment the fit produced is clustered by direction; a
//! cluster carrying enough length is snapped to one direction — its
//! length-weighted mean, exactly 0/90 within the axis snap, exactly
//! perpendicular to a heavier cluster within a degree. A snapped line turns
//! about its node end if it has one, its midpoint otherwise; the joints it
//! shares with its neighbours are re-made. A line with a node at both ends is
//! left alone.

use crate::curves::{intersect, Segment, P};

pub const CLUSTER_DEG: f64 = 0.5;
pub const CLUSTER_MIN_PX: f64 = 40.0;
pub const PERP_DEG: f64 = 0.5;
/// px; see the Python: a snap may move a line's end this far and no further.
pub const END_MOVE_MAX: f64 = 0.15;

fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}

fn add(a: P, b: P) -> P {
    [a[0] + b[0], a[1] + b[1]]
}

fn norm(a: P) -> f64 {
    (a[0] * a[0] + a[1] * a[1]).sqrt()
}

fn dot(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

fn angle_of(p0: P, p1: P) -> f64 {
    let d = sub(p1, p0);
    d[1].atan2(d[0]).to_degrees().rem_euclid(180.0)
}

fn diff(a: f64, b: f64) -> f64 {
    let d = (a - b).abs().rem_euclid(180.0);
    d.min(180.0 - d)
}

fn circular_mean(angles: &[f64], weights: &[f64]) -> f64 {
    let (mut re, mut im) = (0.0f64, 0.0f64);
    for (a, w) in angles.iter().zip(weights) {
        let t = (2.0 * a).to_radians();
        re += w * t.cos();
        im += w * t.sin();
    }
    if (re * re + im * im).sqrt() < 1e-12 {
        return angles[0];
    }
    (im.atan2(re).to_degrees() / 2.0).rem_euclid(180.0)
}

/// `lines` are (angle, length). Per input index, the target angle of its
/// cluster, for lines whose cluster carries at least CLUSTER_MIN_PX.
pub fn cluster_directions(lines: &[(f64, f64)], snap_axis_deg: f64) -> Vec<Option<f64>> {
    let mut out = vec![None; lines.len()];
    if lines.is_empty() {
        return out;
    }
    let mut order: Vec<usize> = (0..lines.len()).collect();
    order.sort_by(|a, b| lines[*a].0.partial_cmp(&lines[*b].0).unwrap());
    let mut clusters: Vec<Vec<usize>> = vec![vec![order[0]]];
    for &i in &order[1..] {
        let last = *clusters.last().unwrap().last().unwrap();
        if diff(lines[i].0, lines[last].0) <= CLUSTER_DEG {
            clusters.last_mut().unwrap().push(i);
        } else {
            clusters.push(vec![i]);
        }
    }
    if clusters.len() > 1 {
        let first = clusters[0][0];
        let last = *clusters.last().unwrap().last().unwrap();
        if diff(lines[first].0, lines[last].0) <= CLUSTER_DEG {
            let tail = clusters.pop().unwrap();
            clusters[0].extend(tail);
        }
    }
    let mut targets: Vec<(f64, f64, Vec<usize>)> = Vec::new();
    for members in clusters {
        let weight: f64 = members.iter().map(|&i| lines[i].1).sum();
        if weight < CLUSTER_MIN_PX {
            continue;
        }
        let angles: Vec<f64> = members.iter().map(|&i| lines[i].0).collect();
        let weights: Vec<f64> = members.iter().map(|&i| lines[i].1).collect();
        let mut angle = circular_mean(&angles, &weights);
        if angle.min(180.0 - angle) <= snap_axis_deg {
            angle = 0.0;
        } else if (angle - 90.0).abs() <= snap_axis_deg {
            angle = 90.0;
        }
        targets.push((angle, weight, members));
    }
    targets.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let mut fixed: Vec<(f64, f64, Vec<usize>)> = Vec::new();
    for (mut angle, weight, members) in targets {
        for (other, _w, _m) in &fixed {
            if diff(angle, (other + 90.0).rem_euclid(180.0)) <= PERP_DEG {
                angle = (other + 90.0).rem_euclid(180.0);
                break;
            }
        }
        fixed.push((angle, weight, members));
    }
    for (angle, _weight, members) in fixed {
        for i in members {
            out[i] = Some(angle);
        }
    }
    out
}

fn unit(angle: f64) -> P {
    [angle.to_radians().cos(), angle.to_radians().sin()]
}

fn line_of(seg: &Segment) -> Option<(P, P)> {
    if let Segment::Line { p0, p1 } = seg {
        let d = sub(*p1, *p0);
        let n = norm(d);
        if n > 1e-9 {
            return Some((*p0, [d[0] / n, d[1] / n]));
        }
    }
    None
}

/// Move `segs[si]`'s end (`p1` when `end_is_p1`) onto the snapped line, and
/// the neighbour's touching end — and a curve's arm there — with it.
fn rejoin(segs: &mut [Segment], si: usize, end_is_p1: bool, neighbour: Option<usize>, new_dir: P, anchor: P) {
    let old = match &segs[si] {
        Segment::Line { p0, p1 } => if end_is_p1 { *p1 } else { *p0 },
        _ => return,
    };
    let t = dot(sub(old, anchor), new_dir);
    let mut corner = add(anchor, [new_dir[0] * t, new_dir[1] * t]);
    if let Some(ni) = neighbour {
        if let Some((q, e)) = line_of(&segs[ni]) {
            if let Some(x) = intersect(anchor, new_dir, q, e) {
                if norm(sub(x, corner)) <= END_MOVE_MAX {
                    corner = x;
                }
            }
        }
    }
    let delta = sub(corner, old);
    if let Segment::Line { p0, p1 } = &mut segs[si] {
        if end_is_p1 {
            *p1 = corner;
        } else {
            *p0 = corner;
        }
    }
    if let Some(ni) = neighbour {
        match &mut segs[ni] {
            Segment::Line { p0, p1 } => {
                if end_is_p1 {
                    *p0 = corner;
                } else {
                    *p1 = corner;
                }
            }
            Segment::Cubic { p0, c1, c2, p1 } => {
                if end_is_p1 {
                    *p0 = corner;
                    *c1 = add(*c1, delta);
                } else {
                    *p1 = corner;
                    *c2 = add(*c2, delta);
                }
            }
            Segment::Arc { p0, p1, .. } => {
                if end_is_p1 {
                    *p0 = corner;
                } else {
                    *p1 = corner;
                }
            }
        }
    }
}

/// Snap the straight segments of every list to their cluster direction. Each
/// entry is (segments, closed); in an open list the first start and last end
/// are nodes and never move. Returns how many lines moved.
pub fn regularize(lists: &mut [(&mut Vec<Segment>, bool)], snap_axis_deg: f64) -> usize {
    let mut entries: Vec<(usize, usize)> = Vec::new();
    let mut lines: Vec<(f64, f64)> = Vec::new();
    for (li, (segs, _closed)) in lists.iter().enumerate() {
        for (si, seg) in segs.iter().enumerate() {
            if let Segment::Line { p0, p1 } = seg {
                let len = norm(sub(*p1, *p0));
                if len > 1e-6 {
                    entries.push((li, si));
                    lines.push((angle_of(*p0, *p1), len));
                }
            }
        }
    }
    let targets = cluster_directions(&lines, snap_axis_deg);
    let mut moved = 0;
    for (k, &(li, si)) in entries.iter().enumerate() {
        let Some(target) = targets[k] else { continue };
        let closed = lists[li].1;
        let segs: &mut Vec<Segment> = lists[li].0;
        let (p0, p1) = match &segs[si] {
            Segment::Line { p0, p1 } => (*p0, *p1),
            _ => continue,
        };
        if diff(angle_of(p0, p1), target) < 1e-9 {
            continue;
        }
        let n = segs.len();
        let start_is_node = !closed && si == 0;
        let end_is_node = !closed && si == n - 1;
        if start_is_node && end_is_node {
            continue;
        }
        let mut prev = if closed || si > 0 { Some((si + n - 1) % n) } else { None };
        let mut next = if closed || si < n - 1 { Some((si + 1) % n) } else { None };
        if n == 1 {
            prev = None;
            next = None;
        }
        let mut new_dir = unit(target);
        if dot(new_dir, sub(p1, p0)) < 0.0 {
            new_dir = [-new_dir[0], -new_dir[1]];
        }
        let anchor = if start_is_node {
            p0
        } else if end_is_node {
            p1
        } else {
            [0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1])]
        };
        let far = [p0, p1]
            .iter()
            .map(|q| {
                let t = dot(sub(*q, anchor), new_dir);
                norm(sub(add(anchor, [new_dir[0] * t, new_dir[1] * t]), *q))
            })
            .fold(0.0f64, f64::max);
        if far > END_MOVE_MAX {
            continue;
        }
        if !start_is_node {
            rejoin(segs, si, false, prev, new_dir, anchor);
        }
        if !end_is_node {
            rejoin(segs, si, true, next, new_dir, anchor);
        }
        moved += 1;
    }
    moved
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clusters_snap_to_axis_and_perpendicular() {
        let t = cluster_directions(&[(0.4, 60.0), (179.95, 30.0), (89.6, 50.0), (45.3, 20.0)], 1.5);
        assert_eq!(t[0], Some(0.0));
        assert_eq!(t[1], Some(0.0));
        assert_eq!(t[2], Some(90.0));
        assert_eq!(t[3], None);
        let t = cluster_directions(&[(30.3, 100.0), (29.9, 90.0), (120.5, 45.0)], 1.5);
        assert!((t[0].unwrap() - t[1].unwrap()).abs() < 1e-9);
        assert!((t[2].unwrap() - (t[0].unwrap() + 90.0)).abs() < 1e-9);
    }

    #[test]
    fn a_line_between_two_nodes_never_turns() {
        let mut a = vec![Segment::Line { p0: [0.0, 0.0], p1: [50.0, 0.4] }];
        let mut b = vec![
            Segment::Line { p0: [0.0, 10.0], p1: [50.0, 10.0] },
            Segment::Cubic { p0: [50.0, 10.0], c1: [55.0, 12.0], c2: [58.0, 18.0], p1: [60.0, 25.0] },
        ];
        let moved = regularize(&mut [(&mut a, false), (&mut b, false)], 1.5);
        assert_eq!(moved, 0);
        assert_eq!(a[0].end(), [50.0, 0.4]);
        let mut c = vec![
            Segment::Line { p0: [0.0, 20.0], p1: [50.0, 20.1] },
            Segment::Cubic { p0: [50.0, 20.1], c1: [55.0, 22.0], c2: [58.0, 28.0], p1: [60.0, 35.0] },
        ];
        let moved = regularize(&mut [(&mut c, false), (&mut b, false)], 1.5);
        assert_eq!(moved, 1);
        assert!((c[0].end()[1] - 20.0).abs() < 1e-9);
        if let Segment::Cubic { p0, c1, .. } = c[1] {
            assert_eq!(p0, c[0].end());
            assert!((c1[1] - 21.9).abs() < 1e-9);
        } else {
            panic!("cubic expected");
        }
    }
}
