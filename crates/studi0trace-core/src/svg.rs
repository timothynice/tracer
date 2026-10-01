//! viewBox normalisation and lightweight stats, ported from `imaging/svg.py`.
use regex::Regex;
use serde::Serialize;
use std::collections::BTreeSet;
use std::sync::OnceLock;

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).unwrap())
}

static ROOT: OnceLock<Regex> = OnceLock::new();
static WIDTH: OnceLock<Regex> = OnceLock::new();
static HEIGHT: OnceLock<Regex> = OnceLock::new();
static VIEWBOX: OnceLock<Regex> = OnceLock::new();
static PATH_TAG: OnceLock<Regex> = OnceLock::new();
static D_ATTR: OnceLock<Regex> = OnceLock::new();
static NUMBER: OnceLock<Regex> = OnceLock::new();
static GRADIENT: OnceLock<Regex> = OnceLock::new();
static FILL_ATTR: OnceLock<Regex> = OnceLock::new();
static FILL_STYLE: OnceLock<Regex> = OnceLock::new();

#[derive(Debug, Clone, Copy, Serialize, PartialEq)]
pub struct Stats {
    pub paths: u64,
    pub nodes: u64,
    pub bytes: u64,
    pub gradients: u64,
    pub unique_fills: u64,
}

/// Remove width/height from the root `<svg>` and set viewBox="0 0 W H".
///
/// Only the root tag is touched; nested elements keep their attributes.
/// Idempotent.
pub fn normalize_dimensions(svg: &str, width: u32, height: u32) -> String {
    let Some(m) = re(&ROOT, r"(?is)<svg\b[^>]*>").find(svg) else {
        return svg.to_string();
    };
    let mut root = m.as_str().to_string();
    for (cell, pat) in [
        (&WIDTH, r#"(?i)\s+width="[^"]*""#),
        (&HEIGHT, r#"(?i)\s+height="[^"]*""#),
        (&VIEWBOX, r#"(?i)\s+viewBox="[^"]*""#),
    ] {
        root = re(cell, pat).replace_all(&root, "").into_owned();
    }
    let closing = if root.trim_end().ends_with("/>") {
        "/>"
    } else {
        ">"
    };
    let body = root[..root.len() - closing.len()].trim_end();
    format!(
        "{}{} viewBox=\"0 0 {width} {height}\"{closing}{}",
        &svg[..m.start()],
        body,
        &svg[m.end()..]
    )
}

/// Compute lightweight statistics about an SVG.
///
/// Coordinate pairs across all path data. Counts implicit polyline/polybezier
/// continuations (which Potrace uses heavily) that a command-letter count misses.
pub fn stats(svg: &str) -> Stats {
    let number = re(&NUMBER, r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?");
    let nodes: u64 = re(&D_ATTR, r#"(?i)\bd="([^"]*)""#)
        .captures_iter(svg)
        .map(|c| number.find_iter(&c[1]).count() as u64 / 2)
        .sum();
    let mut fills: BTreeSet<String> = re(&FILL_ATTR, r#"(?i)\bfill="([^"]*)""#)
        .captures_iter(svg)
        .map(|c| c[1].trim().to_lowercase())
        .collect();
    fills.extend(
        re(&FILL_STYLE, r#"(?i)fill\s*:\s*([^;"']+)"#)
            .captures_iter(svg)
            .map(|c| c[1].trim().to_lowercase()),
    );
    fills.remove("none");
    fills.remove("");
    Stats {
        paths: re(&PATH_TAG, r"(?i)<(?:path|circle|ellipse|rect|polygon|polyline|line)\b")
            .find_iter(svg)
            .count() as u64,
        nodes,
        bytes: svg.len() as u64,
        gradients: re(&GRADIENT, r"(?i)<(?:linear|radial)Gradient\b")
            .find_iter(svg)
            .count() as u64,
        unique_fills: fills.len() as u64,
    }
}
