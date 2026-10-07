//! How far a redraw moved the image, from the core's own measures (no new metric to port): the redraw resized to
//! the original's size, both composited on white (inside `Reference`), and `Reference::fidelity`'s edge F1 (Canny
//! edges matched within 2 px) and mean CIEDE2000. The verdict is the worse of the two scales. A `Reference` costs
//! about 34 bytes a pixel: callers run this off the main thread.

use super::error;
use crate::error::CommandError;
use serde::Serialize;
use studi0trace_core::resample::{resize_rgba, Filter};
use studi0trace_core::scorecard::Reference;

pub const CLOSE_F1: f64 = 0.95;
pub const CLOSE_DE: f64 = 2.5;
pub const NOTICEABLE_F1: f64 = 0.80;
pub const NOTICEABLE_DE: f64 = 5.0;

/// Close, Noticeable or Large, in that order of worse.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Verdict {
    Close,
    Noticeable,
    Large,
}

/// The drift of a redraw from its original: `{"edgeF1", "deltaE", "verdict"}`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Drift {
    pub edge_f1: f64,
    pub delta_e: f64,
    pub verdict: Verdict,
}

/// The worse of the edge scale and the colour scale (a NaN on either is Large).
pub fn verdict(edge_f1: f64, delta_e: f64) -> Verdict {
    let edges = if edge_f1 >= CLOSE_F1 {
        Verdict::Close
    } else if edge_f1 >= NOTICEABLE_F1 {
        Verdict::Noticeable
    } else {
        Verdict::Large
    };
    let colour = if delta_e <= CLOSE_DE {
        Verdict::Close
    } else if delta_e <= NOTICEABLE_DE {
        Verdict::Noticeable
    } else {
        Verdict::Large
    };
    edges.max(colour)
}

/// The drift of a `rw` x `rh` RGBA redraw from the `w` x `h` RGBA original.
pub fn measure(original: &[u8], w: u32, h: u32, redraw: &[u8], rw: u32, rh: u32) -> Result<Drift, CommandError> {
    let fail = |why: String| {
        eprintln!("studi0trace: the redraw could not be compared with the original: {why}");
        error("bad_reply")
    };
    let resized = if (rw, rh) == (w, h) { redraw.to_vec() } else { resize_rgba(redraw, rw, rh, w, h, Filter::Lanczos).map_err(|e| fail(e.to_string()))? };
    let reference = Reference::new(original, h as usize, w as usize).map_err(|e| fail(e.to_string()))?;
    let card = reference.fidelity(&resized).map_err(|e| fail(e.to_string()))?;
    let edge_f1 = card.get("edge_f1").and_then(|v| v.as_f64()).unwrap_or(f64::NAN);
    let delta_e = card.get("delta_e_mean").and_then(|v| v.as_f64()).unwrap_or(f64::NAN);
    Ok(Drift { edge_f1, delta_e, verdict: verdict(edge_f1, delta_e) })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A white `w` x `h` RGBA image with a red square of `side` at `at`.
    fn square(w: u32, h: u32, at: (u32, u32), side: u32) -> Vec<u8> {
        (0..w * h)
            .flat_map(|i| -> [u8; 4] {
                let (x, y) = (i % w, i / w);
                if x >= at.0 && x < at.0 + side && y >= at.1 && y < at.1 + side {
                    [210, 40, 40, 255]
                } else {
                    [255, 255, 255, 255]
                }
            })
            .collect()
    }

    #[test]
    fn the_verdict_is_the_worse_of_the_two_scales() {
        assert_eq!(verdict(1.0, 0.0), Verdict::Close);
        assert_eq!(verdict(0.95, 2.5), Verdict::Close);
        assert_eq!(verdict(0.9499, 2.5), Verdict::Noticeable);
        assert_eq!(verdict(0.95, 2.51), Verdict::Noticeable);
        assert_eq!(verdict(0.80, 5.0), Verdict::Noticeable);
        assert_eq!(verdict(0.7999, 0.0), Verdict::Large);
        assert_eq!(verdict(1.0, 5.01), Verdict::Large);
        // the spec's calibration on the wave-lockup asset: our trace, and Tim's ChatGPT redraw
        assert_eq!(verdict(0.997, 1.5), Verdict::Close);
        assert_eq!(verdict(0.59, 3.8), Verdict::Large);
        // a number that did not come out is no reassurance
        assert_eq!(verdict(f64::NAN, 0.0), Verdict::Large);
        assert_eq!(verdict(1.0, f64::NAN), Verdict::Large);
    }

    #[test]
    fn the_same_image_is_close() {
        let img = square(64, 64, (20, 20), 24);
        let d = measure(&img, 64, 64, &img, 64, 64).unwrap();
        assert_eq!((d.edge_f1, d.delta_e, d.verdict), (1.0, 0.0, Verdict::Close));
    }

    #[test]
    fn a_larger_redraw_is_measured_at_the_originals_size() {
        let d = measure(&square(64, 64, (20, 20), 24), 64, 64, &square(256, 256, (80, 80), 96), 256, 256).unwrap();
        assert_eq!(d.verdict, Verdict::Close, "{d:?}");
    }

    #[test]
    fn a_moved_shape_is_large() {
        let d = measure(&square(64, 64, (8, 8), 20), 64, 64, &square(64, 64, (36, 36), 20), 64, 64).unwrap();
        assert_eq!(d.verdict, Verdict::Large, "{d:?}");
    }

    #[test]
    fn a_redraw_of_no_pixels_is_a_bad_reply() {
        assert_eq!(measure(&square(4, 4, (0, 0), 1), 4, 4, &[], 0, 0).unwrap_err().code(), Some("bad_reply"));
    }

    #[test]
    fn drift_is_the_commands_json() {
        let d = Drift { edge_f1: 0.59, delta_e: 3.8, verdict: Verdict::Large };
        assert_eq!(serde_json::to_value(d).unwrap(), serde_json::json!({"edgeF1": 0.59, "deltaE": 3.8, "verdict": "large"}));
        assert_eq!(serde_json::to_value(Verdict::Noticeable).unwrap(), "noticeable");
    }
}
