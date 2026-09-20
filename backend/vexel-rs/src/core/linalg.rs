//! Small dense linear algebra: least squares, symmetric eigendecomposition and
//! linear solves. Everything here works on row-major `Vec<f64>` blocks that are
//! at most a few hundred rows by six columns, so the simple algorithms are the
//! right ones.

/// Row-major matrix.
#[derive(Clone, Debug)]
pub struct Mat {
    pub rows: usize,
    pub cols: usize,
    pub d: Vec<f64>,
}

impl Mat {
    pub fn zeros(rows: usize, cols: usize) -> Self {
        Mat { rows, cols, d: vec![0.0; rows * cols] }
    }

    #[inline]
    pub fn at(&self, r: usize, c: usize) -> f64 {
        self.d[r * self.cols + c]
    }

    #[inline]
    pub fn set(&mut self, r: usize, c: usize, v: f64) {
        self.d[r * self.cols + c] = v;
    }
}

/// Least squares `min ‖A·X − B‖` by Householder QR with column pivoting.
///
/// Normal equations would be simpler, but they square the condition number,
/// and two of the designs here are badly conditioned on purpose: the radial
/// surrogate fits a cubic in `r` (a Vandermonde basis) and the ramp fits a hat
/// basis whose columns are nearly dependent when a knot interval holds few
/// samples. The gradient stops that came out ten levels off the Python's were
/// exactly that loss of digits.
///
/// Rank deficiency is resolved by the *basic* solution — the trailing
/// coefficients are set to zero — where `numpy.linalg.lstsq` takes the
/// minimum-norm one. They agree wherever the design has full rank, which is
/// every case that matters for the fit.
pub fn lstsq(a: &Mat, b: &Mat) -> Mat {
    let (n, p, k) = (a.rows, a.cols, b.cols);
    debug_assert_eq!(b.rows, n);
    if n == 0 || p == 0 {
        return Mat::zeros(p, k);
    }

    let mut r = a.d.clone(); // n x p, overwritten with R
    let mut q = b.d.clone(); // n x k, overwritten with Qᵀ·B
    let mut perm: Vec<usize> = (0..p).collect();
    let mut colnorm: Vec<f64> = (0..p)
        .map(|j| (0..n).map(|i| r[i * p + j] * r[i * p + j]).sum::<f64>())
        .collect();

    let steps = p.min(n);
    let mut rank = steps;
    let mut r00 = 0.0f64;
    for step in 0..steps {
        // pivot on the largest remaining column norm
        let mut best = step;
        for j in step + 1..p {
            if colnorm[j] > colnorm[best] {
                best = j;
            }
        }
        if best != step {
            perm.swap(step, best);
            colnorm.swap(step, best);
            for i in 0..n {
                r.swap(i * p + step, i * p + best);
            }
        }

        // Householder reflector for column `step` below the diagonal
        let mut alpha = 0.0f64;
        for i in step..n {
            alpha += r[i * p + step] * r[i * p + step];
        }
        alpha = alpha.sqrt();
        if step == 0 {
            r00 = alpha;
        }
        if alpha <= 1e-300 || (r00 > 0.0 && alpha <= 1e-12 * r00) {
            rank = step;
            break;
        }
        if r[step * p + step] > 0.0 {
            alpha = -alpha;
        }
        let mut v: Vec<f64> = (step..n).map(|i| r[i * p + step]).collect();
        v[0] -= alpha;
        let vnorm2: f64 = v.iter().map(|x| x * x).sum();
        if vnorm2 <= 1e-300 {
            r[step * p + step] = alpha;
            continue;
        }

        // apply (I − 2vvᵀ/vᵀv) to the remaining columns of R and to Qᵀ·B
        for j in step..p {
            let dot: f64 = v.iter().enumerate().map(|(t, vt)| vt * r[(step + t) * p + j]).sum();
            let f = 2.0 * dot / vnorm2;
            for (t, vt) in v.iter().enumerate() {
                r[(step + t) * p + j] -= f * vt;
            }
        }
        for j in 0..k {
            let dot: f64 = v.iter().enumerate().map(|(t, vt)| vt * q[(step + t) * k + j]).sum();
            let f = 2.0 * dot / vnorm2;
            for (t, vt) in v.iter().enumerate() {
                q[(step + t) * k + j] -= f * vt;
            }
        }
        r[step * p + step] = alpha;
        for i in step + 1..n {
            r[i * p + step] = 0.0;
        }
        for j in step + 1..p {
            colnorm[j] = (step + 1..n).map(|i| r[i * p + j] * r[i * p + j]).sum();
        }
    }

    // back-substitute the leading `rank` unknowns
    let mut x = Mat::zeros(p, k);
    for col in 0..k {
        let mut y = vec![0.0f64; rank];
        for i in (0..rank).rev() {
            let mut s = q[i * k + col];
            for j in i + 1..rank {
                s -= r[i * p + j] * y[j];
            }
            let d = r[i * p + i];
            y[i] = if d.abs() > 1e-300 { s / d } else { 0.0 };
        }
        for i in 0..rank {
            x.set(perm[i], col, y[i]);
        }
    }
    x
}

/// Cholesky solve for a symmetric positive-definite `a`.
pub fn solve_spd(a: &Mat, b: &Mat) -> Option<Mat> {
    let n = a.rows;
    let mut l = vec![0.0f64; n * n];
    for i in 0..n {
        for j in 0..=i {
            let mut s = a.at(i, j);
            for k in 0..j {
                s -= l[i * n + k] * l[j * n + k];
            }
            if i == j {
                if s <= 0.0 {
                    return None;
                }
                l[i * n + j] = s.sqrt();
            } else {
                l[i * n + j] = s / l[j * n + j];
            }
        }
    }
    let k = b.cols;
    let mut x = Mat::zeros(n, k);
    for c in 0..k {
        let mut y = vec![0.0f64; n];
        for i in 0..n {
            let mut s = b.at(i, c);
            for j in 0..i {
                s -= l[i * n + j] * y[j];
            }
            y[i] = s / l[i * n + i];
        }
        for i in (0..n).rev() {
            let mut s = y[i];
            for j in i + 1..n {
                s -= l[j * n + i] * x.at(j, c);
            }
            x.set(i, c, s / l[i * n + i]);
        }
    }
    Some(x)
}

/// General square solve by Gaussian elimination with partial pivoting.
pub fn solve(a: &Mat, b: &Mat) -> Option<Mat> {
    let n = a.rows;
    let k = b.cols;
    let mut m = a.d.clone();
    let mut r = b.d.clone();
    for col in 0..n {
        let mut piv = col;
        let mut best = m[col * n + col].abs();
        for row in col + 1..n {
            let v = m[row * n + col].abs();
            if v > best {
                best = v;
                piv = row;
            }
        }
        if best < 1e-300 {
            return None;
        }
        if piv != col {
            for c in 0..n {
                m.swap(col * n + c, piv * n + c);
            }
            for c in 0..k {
                r.swap(col * k + c, piv * k + c);
            }
        }
        let d = m[col * n + col];
        for row in col + 1..n {
            let f = m[row * n + col] / d;
            if f == 0.0 {
                continue;
            }
            for c in col..n {
                m[row * n + c] -= f * m[col * n + c];
            }
            for c in 0..k {
                r[row * k + c] -= f * r[col * k + c];
            }
        }
    }
    let mut x = Mat::zeros(n, k);
    for c in 0..k {
        for row in (0..n).rev() {
            let mut s = r[row * k + c];
            for j in row + 1..n {
                s -= m[row * n + j] * x.at(j, c);
            }
            x.set(row, c, s / m[row * n + row]);
        }
    }
    Some(x)
}

/// Eigenvalues and eigenvectors of a symmetric 2×2, ascending by eigenvalue —
/// the ordering `numpy.linalg.eigh` uses. Returns (values, vectors), where
/// `vectors[i]` belongs to `values[i]`.
pub fn eigh2(a: f64, b: f64, d: f64) -> ([f64; 2], [[f64; 2]; 2]) {
    // [[a, b], [b, d]]
    let tr = a + d;
    let det = a * d - b * b;
    let disc = ((tr * tr / 4.0) - det).max(0.0).sqrt();
    let l1 = tr / 2.0 - disc; // smaller
    let l2 = tr / 2.0 + disc;
    let vec_for = |l: f64| -> [f64; 2] {
        let (mut x, mut y) = (b, l - a);
        if x.abs() < 1e-14 && y.abs() < 1e-14 {
            x = l - d;
            y = b;
        }
        let n = (x * x + y * y).sqrt();
        if n < 1e-300 {
            [1.0, 0.0]
        } else {
            [x / n, y / n]
        }
    };
    let v1 = vec_for(l1);
    // a symmetric 2×2 has orthogonal eigenvectors, so the second is the
    // perpendicular of the first — stable even when the matrix is near a
    // multiple of the identity and `vec_for` would be ill-conditioned
    let v2 = [-v1[1], v1[0]];
    ([l1, l2], [v1, v2])
}

/// Eigenvalues and eigenvectors of a symmetric 3×3 by the Jacobi rotation
/// method, ascending by eigenvalue. Returns (values, eigenvector columns).
pub fn eigh3(m: [[f64; 3]; 3]) -> ([f64; 3], [[f64; 3]; 3]) {
    let mut a = m;
    let mut v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
    for _ in 0..64 {
        let mut off = 0.0;
        for p in 0..3 {
            for q in p + 1..3 {
                off += a[p][q] * a[p][q];
            }
        }
        if off < 1e-30 {
            break;
        }
        for p in 0..3 {
            for q in p + 1..3 {
                if a[p][q].abs() < 1e-300 {
                    continue;
                }
                let theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q]);
                let t = theta.signum() / (theta.abs() + (theta * theta + 1.0).sqrt());
                let c = 1.0 / (t * t + 1.0).sqrt();
                let s = t * c;
                for k in 0..3 {
                    let (akp, akq) = (a[k][p], a[k][q]);
                    a[k][p] = c * akp - s * akq;
                    a[k][q] = s * akp + c * akq;
                }
                for k in 0..3 {
                    let (apk, aqk) = (a[p][k], a[q][k]);
                    a[p][k] = c * apk - s * aqk;
                    a[q][k] = s * apk + c * aqk;
                }
                for k in 0..3 {
                    let (vkp, vkq) = (v[k][p], v[k][q]);
                    v[k][p] = c * vkp - s * vkq;
                    v[k][q] = s * vkp + c * vkq;
                }
            }
        }
    }
    let mut idx = [0usize, 1, 2];
    let vals = [a[0][0], a[1][1], a[2][2]];
    idx.sort_by(|i, j| vals[*i].partial_cmp(&vals[*j]).unwrap());
    let out_vals = [vals[idx[0]], vals[idx[1]], vals[idx[2]]];
    let mut out_vecs = [[0.0; 3]; 3];
    for (col, src) in idx.iter().enumerate() {
        for row in 0..3 {
            out_vecs[row][col] = v[row][*src];
        }
    }
    (out_vals, out_vecs)
}

/// Total-least-squares direction of a 2-D point set: the principal axis.
pub fn principal_direction(pts: &[[f64; 2]]) -> ([f64; 2], [f64; 2]) {
    let n = pts.len() as f64;
    let cx = pts.iter().map(|p| p[0]).sum::<f64>() / n;
    let cy = pts.iter().map(|p| p[1]).sum::<f64>() / n;
    let (mut sxx, mut sxy, mut syy) = (0.0, 0.0, 0.0);
    for p in pts {
        let (dx, dy) = (p[0] - cx, p[1] - cy);
        sxx += dx * dx;
        sxy += dx * dy;
        syy += dy * dy;
    }
    let (vals, vecs) = eigh2(sxx, sxy, syy);
    let d = if vals[1] >= vals[0] { vecs[1] } else { vecs[0] };
    ([cx, cy], d)
}
