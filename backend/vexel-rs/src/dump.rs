//! Intermediate dumps, written when `VEXEL_DUMP` names a directory.
//!
//! `tools/diffcheck.py` feeds both implementations one input per stage; this
//! is for the other question — what did the engine *actually* hand each stage
//! on a real trace — and pairs with the same dump in the Python engine.

use crate::core::grid::Grid;
use crate::curves::Segment;
use crate::topology::Boundary;
use std::io::Write;

fn dir() -> Option<std::path::PathBuf> {
    std::env::var_os("VEXEL_DUMP").map(std::path::PathBuf::from)
}

/// A label map as text: `h w` then one row per line.
pub fn labels(name: &str, g: &Grid<i32>) {
    let Some(d) = dir() else { return };
    let mut f = match std::fs::File::create(d.join(format!("{name}.txt"))) {
        Ok(f) => std::io::BufWriter::new(f),
        Err(_) => return,
    };
    let _ = writeln!(f, "{} {}", g.h, g.w);
    for r in 0..g.h {
        let row: Vec<String> = (0..g.w).map(|c| g.data[r * g.w + c].to_string()).collect();
        let _ = writeln!(f, "{}", row.join(" "));
    }
}

/// Append a line of free text to `<name>.txt`.
pub fn text(name: &str, line: &str) {
    let Some(d) = dir() else { return };
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(d.join(format!("{name}.txt"))) {
        let _ = f.write_all(line.as_bytes());
    }
}

fn seg(s: &Segment) -> String {
    match s {
        Segment::Line { p0, p1 } => format!("L {:.6} {:.6} {:.6} {:.6}", p0[0], p0[1], p1[0], p1[1]),
        Segment::Cubic { p0, c1, c2, p1 } => format!(
            "C {:.6} {:.6} {:.6} {:.6} {:.6} {:.6} {:.6} {:.6}",
            p0[0], p0[1], c1[0], c1[1], c2[0], c2[1], p1[0], p1[1]
        ),
        Segment::Arc { p0, p1, r, large, sweep } => format!(
            "A {:.6} {:.6} {:.6} {:.6} {:.6} {} {}",
            p0[0], p0[1], p1[0], p1[1], r, *large as u8, *sweep as u8
        ),
    }
}

/// Every arc of the boundary graph: pair, ends' state, placed vertices, fitted
/// segments and the bled copy, one arc per block, in the graph's own order.
pub fn arcs(name: &str, bnd: &Boundary) {
    let Some(d) = dir() else { return };
    let mut f = match std::fs::File::create(d.join(format!("{name}.txt"))) {
        Ok(f) => std::io::BufWriter::new(f),
        Err(_) => return,
    };
    for a in &bnd.arcs {
        let t = |t: Option<[f64; 2]>| match t {
            Some(v) => format!("{:.6},{:.6}", v[0], v[1]),
            None => "-".to_string(),
        };
        let _ = writeln!(
            f,
            "arc {} {} n={} closed={} t0={} t1={} tip0={} tip1={} trim0={:.4} trim1={:.4} sliver={} mirror={}",
            a.pair.0, a.pair.1, a.pts.len(), a.closed() as u8, t(a.t0), t(a.t1), a.tip0 as u8, a.tip1 as u8,
            a.trim0, a.trim1, a.sliver.as_ref().map_or(0, |s| s.iter().filter(|b| **b).count()),
            a.mirror.is_some() as u8
        );
        let pts: Vec<String> = a.pts.iter().map(|p| format!("{:.6},{:.6}", p[0], p[1])).collect();
        let _ = writeln!(f, "  pts {}", pts.join(" "));
        for s in &a.segments {
            let _ = writeln!(f, "  seg {}", seg(s));
        }
        for s in &a.under {
            let _ = writeln!(f, "  under {}", seg(s));
        }
    }
}
