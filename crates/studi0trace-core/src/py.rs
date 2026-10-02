//! Python's own semantics for the builtins and the `math` functions the ported code leans on,
//! in one place: where a NaN goes in `max` and `min`, how `round` rounds, what `repr` writes,
//! the sign of `%`, and what `int()` and `math.ceil` raise. numpy's (its pairwise sum, its
//! `round`, its percentile) stay with the code that uses them.

/// What `int()` and `math.ceil` raise for a float that is not finite, in the Python's words:
/// `ValueError` for NaN, `OverflowError` for an infinity.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NotIntegral(pub &'static str);

/// The check `int()` and `math.ceil` make: `x` when it is finite.
fn integral(x: f64) -> Result<f64, NotIntegral> {
    if x.is_nan() {
        Err(NotIntegral("cannot convert float NaN to integer"))
    } else if x.is_infinite() {
        Err(NotIntegral("cannot convert float infinity to integer"))
    } else {
        Ok(x)
    }
}

/// `max(a, b)` of two floats: `b` only when it is greater, so a NaN survives only as `a`.
pub(crate) fn max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `min(a, b)`: `b` only when it is less.
pub(crate) fn min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `max(floats)`: the first, replaced by each later one that is greater (NaN is never greater,
/// and nothing is greater than NaN). `None` where the Python raises, on nothing.
pub(crate) fn max_of(values: impl IntoIterator<Item = f64>) -> Option<f64> {
    values.into_iter().reduce(max)
}

/// `min(floats)`, the first least.
pub(crate) fn min_of(values: impl IntoIterator<Item = f64>) -> Option<f64> {
    values.into_iter().reduce(min)
}

/// `round(x, ndigits)` for a float: the exact value of `x` rounded correctly to `ndigits`
/// decimals (a tie goes to the even digit), read back as the nearest double. NaN and the
/// infinities are themselves, and a negative that rounds to nothing is `-0.0`.
///
/// Rust formats a float exactly (its decimal expansion is not truncated at 17 digits) and rounds
/// that to even, which is what CPython's `dtoa` does; the string is parsed back correctly rounded.
/// `tests/auto.rs` holds it to the Python's over a thousand numbers (decimals as typed, exact
/// ties, the doubles either side of them, both signs, tiny and huge), and it was compared with a
/// million more when it was written.
pub fn round(x: f64, ndigits: u32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits as usize, x).parse().unwrap_or(x)
}

/// `int(round(x))`: ties to even.
pub(crate) fn round_int(x: f64) -> Result<i64, NotIntegral> {
    Ok(integral(x)?.round_ties_even() as i64)
}

/// `math.ceil`.
pub(crate) fn ceil(x: f64) -> Result<f64, NotIntegral> {
    Ok(integral(x)?.ceil())
}

/// `%` of two floats: the remainder with the sign of the divisor.
pub(crate) fn rem(a: f64, b: f64) -> f64 {
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

/// `repr(x)` (and `str(x)`) of a float: the shortest digits that read back as `x`, in fixed
/// notation from 1e-4 to below 1e16 (with a `.0` when whole) and as `1.5e+16`, `1e-05` outside it.
pub(crate) fn repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return (if x > 0.0 { "inf" } else { "-inf" }).into();
    }
    // `{:e}` is the shortest round-trip digits too: "-1.2345e-7", "3e0"
    let sci = format!("{x:e}");
    let (mantissa, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let (sign, mantissa) = mantissa.strip_prefix('-').map_or(("", mantissa), |m| ("-", m));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if !(-4..16).contains(&exp) {
        let (head, tail) = digits.split_at(1);
        let dot = if tail.is_empty() { String::new() } else { format!(".{tail}") };
        let esign = if exp < 0 { '-' } else { '+' };
        return format!("{sign}{head}{dot}e{esign}{:02}", exp.abs());
    }
    if exp < 0 {
        return format!("{sign}0.{}{digits}", "0".repeat((-exp - 1) as usize));
    }
    let point = exp as usize + 1;
    if digits.len() <= point {
        format!("{sign}{digits}{}.0", "0".repeat(point - digits.len()))
    } else {
        format!("{sign}{}.{}", &digits[..point], &digits[point..])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn max_and_min_keep_the_first_of_a_nan() {
        let nan = f64::NAN;
        assert!(max(nan, 1.0).is_nan() && min(nan, 1.0).is_nan());
        assert_eq!((max(1.0, nan), min(1.0, nan)), (1.0, 1.0));
        assert_eq!((max_of([1.0, nan, 3.0]), min_of([3.0, nan, 1.0])), (Some(3.0), Some(1.0)));
        assert!(max_of([nan, 3.0]).unwrap().is_nan());
        assert_eq!(max_of([]), None);
        // equal values: the first is kept, which tells -0.0 from 0.0
        assert!(max(-0.0, 0.0).is_sign_negative() && min(0.0, -0.0).is_sign_positive());
    }

    #[test]
    fn int_and_ceil_raise_on_what_is_not_finite() {
        assert_eq!((round_int(2.5), round_int(3.5), round_int(-0.5)), (Ok(2), Ok(4), Ok(0)));
        assert_eq!(ceil(1.25), Ok(2.0));
        assert_eq!(round_int(f64::NAN), Err(NotIntegral("cannot convert float NaN to integer")));
        assert_eq!(ceil(f64::NEG_INFINITY), Err(NotIntegral("cannot convert float infinity to integer")));
    }

    #[test]
    fn rem_has_the_sign_of_the_divisor() {
        assert_eq!((rem(-1.0, 90.0), rem(1.0, -90.0), rem(181.0, 90.0)), (89.0, -89.0, 1.0));
        assert!(rem(-90.0, 90.0).is_sign_positive() && rem(90.0, -90.0).is_sign_negative());
    }

    #[test]
    fn repr_is_pythons() {
        for (x, s) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (1.5e16, "1.5e+16"),
            (1e16, "1e+16"),
            (9999999999999998.0, "9999999999999998.0"),
            (-123.456, "-123.456"),
            (f64::INFINITY, "inf"),
        ] {
            assert_eq!(repr(x), s, "{x:e}");
        }
    }
}
