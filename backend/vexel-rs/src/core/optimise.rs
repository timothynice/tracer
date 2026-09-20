//! `scipy.optimize.minimize(method="Nelder-Mead")`, step for step.
//!
//! The reflection / expansion / contraction / shrink cascade and the
//! `xatol`/`fatol` stopping test are scipy's, because the shadow fit lands in a
//! shallow valley where a differently-shaped simplex settles somewhere else and
//! flips a filter's accept/reject decision.

pub struct NmOptions {
    pub xatol: f64,
    pub fatol: f64,
    pub maxiter: usize,
    pub maxfev: usize,
}

impl Default for NmOptions {
    fn default() -> Self {
        NmOptions { xatol: 1e-4, fatol: 1e-4, maxiter: usize::MAX, maxfev: usize::MAX }
    }
}

/// scipy's default simplex: each axis stepped by 5 % of its value, or 0.00025
/// when that value is zero.
pub fn default_simplex(x0: &[f64]) -> Vec<Vec<f64>> {
    const NONZDELT: f64 = 0.05;
    const ZDELT: f64 = 0.00025;
    let n = x0.len();
    let mut sim = Vec::with_capacity(n + 1);
    sim.push(x0.to_vec());
    for k in 0..n {
        let mut y = x0.to_vec();
        if y[k] != 0.0 {
            y[k] *= 1.0 + NONZDELT;
        } else {
            y[k] = ZDELT;
        }
        sim.push(y);
    }
    sim
}

/// Minimise `f`, returning the best point found.
pub fn nelder_mead<F: FnMut(&[f64]) -> f64>(
    mut f: F,
    simplex0: Vec<Vec<f64>>,
    opts: &NmOptions,
) -> Vec<f64> {
    const RHO: f64 = 1.0;
    const CHI: f64 = 2.0;
    const PSI: f64 = 0.5;
    const SIGMA: f64 = 0.5;

    let n = simplex0[0].len();
    let mut sim = simplex0;
    let mut nfev = 0usize;
    let mut fsim: Vec<f64> = sim
        .iter()
        .map(|p| {
            nfev += 1;
            f(p)
        })
        .collect();

    let sort = |sim: &mut Vec<Vec<f64>>, fsim: &mut Vec<f64>| {
        let mut idx: Vec<usize> = (0..fsim.len()).collect();
        idx.sort_by(|a, b| fsim[*a].partial_cmp(&fsim[*b]).unwrap_or(std::cmp::Ordering::Equal));
        *sim = idx.iter().map(|i| sim[*i].clone()).collect();
        *fsim = idx.iter().map(|i| fsim[*i]).collect();
    };
    sort(&mut sim, &mut fsim);

    let mut iterations = 0usize;
    while iterations < opts.maxiter && nfev < opts.maxfev {
        let max_dx = sim[1..]
            .iter()
            .flat_map(|p| p.iter().zip(sim[0].iter()).map(|(a, b)| (a - b).abs()))
            .fold(0.0f64, f64::max);
        let max_df = fsim[1..].iter().map(|v| (fsim[0] - v).abs()).fold(0.0f64, f64::max);
        if max_dx <= opts.xatol && max_df <= opts.fatol {
            break;
        }

        let mut xbar = vec![0.0; n];
        for p in &sim[..n] {
            for (i, v) in p.iter().enumerate() {
                xbar[i] += v / n as f64;
            }
        }
        let last = sim[n].clone();
        let combine = |k: f64| -> Vec<f64> {
            (0..n).map(|i| (1.0 + k) * xbar[i] - k * last[i]).collect()
        };

        let xr = combine(RHO);
        let fxr = {
            nfev += 1;
            f(&xr)
        };
        let mut doshrink = false;

        if fxr < fsim[0] {
            let xe = combine(RHO * CHI);
            let fxe = {
                nfev += 1;
                f(&xe)
            };
            if fxe < fxr {
                sim[n] = xe;
                fsim[n] = fxe;
            } else {
                sim[n] = xr;
                fsim[n] = fxr;
            }
        } else if fxr < fsim[n - 1] {
            sim[n] = xr;
            fsim[n] = fxr;
        } else if fxr < fsim[n] {
            let xc = combine(PSI * RHO);
            let fxc = {
                nfev += 1;
                f(&xc)
            };
            if fxc <= fxr {
                sim[n] = xc;
                fsim[n] = fxc;
            } else {
                doshrink = true;
            }
        } else {
            let xcc: Vec<f64> = (0..n).map(|i| (1.0 - PSI) * xbar[i] + PSI * last[i]).collect();
            let fxcc = {
                nfev += 1;
                f(&xcc)
            };
            if fxcc < fsim[n] {
                sim[n] = xcc;
                fsim[n] = fxcc;
            } else {
                doshrink = true;
            }
        }

        if doshrink {
            let base = sim[0].clone();
            for j in 1..=n {
                sim[j] = (0..n).map(|i| base[i] + SIGMA * (sim[j][i] - base[i])).collect();
                nfev += 1;
                fsim[j] = f(&sim[j]);
            }
        }

        sort(&mut sim, &mut fsim);
        iterations += 1;
    }
    sim[0].clone()
}
