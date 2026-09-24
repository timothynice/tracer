//! numpy's PCG64 and `Generator.choice(n, k, replace=False)`.
//!
//! `fills.fit_fill` subsamples large regions with
//! `np.random.default_rng(1234).choice(n, 25000, replace=False)`, and which
//! pixels it picks changes the fitted gradient. Reproducing the draw is the
//! only way to tell a real difference in the fit from sampling noise, so this
//! is PCG64's `xsl_rr_128_64` output function, numpy's Lemire bounded
//! generation, and both of `choice`'s roads: the tail shuffle of the whole
//! range numpy takes when more than a fiftieth of a large population is
//! wanted, and Floyd's algorithm with the same hash-set probing and the same
//! final shuffle otherwise.
//!
//! The seed is always the literal 1234, so the 128-bit state SeedSequence
//! derives for it is embedded rather than reimplementing SeedSequence.

/// `np.random.default_rng(1234).bit_generator.state`.
const SEED_1234_STATE: u128 = 0x160a_d840_06fe_21ea_f69b_873d_9fe4_5409;
const SEED_1234_INC: u128 = 0x50c8_fb16_3c7c_ea4e_d0f5_1ce6_006e_4325;
const PCG_MULT: u128 = 0x2360_ED05_1FC6_5DA4_4385_DF64_9FCC_F645;

pub struct Pcg64 {
    state: u128,
    inc: u128,
    has_uint32: bool,
    uinteger: u32,
}

impl Pcg64 {
    /// The generator `np.random.default_rng(1234)` starts from.
    pub fn seed_1234() -> Self {
        Pcg64 { state: SEED_1234_STATE, inc: SEED_1234_INC, has_uint32: false, uinteger: 0 }
    }

    #[inline]
    fn step(&mut self) {
        self.state = self.state.wrapping_mul(PCG_MULT).wrapping_add(self.inc);
    }

    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        self.step();
        let s = self.state;
        let xored = ((s >> 64) as u64) ^ (s as u64);
        let rot = (s >> 122) as u32;
        xored.rotate_right(rot)
    }

    #[inline]
    pub fn next_u32(&mut self) -> u32 {
        if self.has_uint32 {
            self.has_uint32 = false;
            return self.uinteger;
        }
        let next = self.next_u64();
        self.has_uint32 = true;
        self.uinteger = (next >> 32) as u32;
        next as u32
    }

    /// numpy's `random_bounded_uint64(off = 0, rng = max_inclusive)`.
    pub fn bounded(&mut self, max_inclusive: u64) -> u64 {
        if max_inclusive == 0 {
            return 0;
        }
        if max_inclusive <= 0xFFFF_FFFF {
            if max_inclusive == 0xFFFF_FFFF {
                return self.next_u32() as u64;
            }
            return self.lemire_u32(max_inclusive as u32) as u64;
        }
        if max_inclusive == u64::MAX {
            return self.next_u64();
        }
        self.lemire_u64(max_inclusive)
    }

    fn lemire_u32(&mut self, rng: u32) -> u32 {
        let rng_excl = (rng as u64) + 1;
        let mut m = (self.next_u32() as u64) * rng_excl;
        let mut leftover = m as u32;
        if (leftover as u64) < rng_excl {
            let threshold = ((u32::MAX - rng) as u64 % rng_excl) as u32;
            while leftover < threshold {
                m = (self.next_u32() as u64) * rng_excl;
                leftover = m as u32;
            }
        }
        (m >> 32) as u32
    }

    fn lemire_u64(&mut self, rng: u64) -> u64 {
        let rng_excl = rng + 1;
        let mut m = (self.next_u64() as u128) * (rng_excl as u128);
        let mut leftover = m as u64;
        if leftover < rng_excl {
            let threshold = (u64::MAX - rng) % rng_excl;
            while leftover < threshold {
                m = (self.next_u64() as u128) * (rng_excl as u128);
                leftover = m as u64;
            }
        }
        (m >> 64) as u64
    }
}

fn gen_mask(mut max: u64) -> u64 {
    max |= max >> 1;
    max |= max >> 2;
    max |= max >> 4;
    max |= max >> 8;
    max |= max >> 16;
    max |= max >> 32;
    max
}

/// numpy's `_shuffle_int(n, first, data)`: Fisher-Yates over `data[first..n]`
/// from the top, each drawing its partner from everything below it.
fn shuffle_int(rng: &mut Pcg64, n: usize, first: usize, data: &mut [u64]) {
    for i in (first..n).rev() {
        let j = rng.bounded(i as u64) as usize;
        data.swap(i, j);
    }
}

/// `Generator.choice(pop_size, size, replace=False)` (shuffle=True). numpy
/// takes one of two roads: past 10000 with more than a fiftieth of the
/// population wanted, a tail shuffle of the whole range; otherwise Floyd's
/// algorithm, then a Fisher-Yates shuffle of the result. `fit_fill` asks for
/// 25000 of a region, which is the tail shuffle for every region under 1.25
/// M pixels; taking Floyd's there fitted large gradients to other pixels
/// than the Python (silverpeak-badge's backdrop ramp 3 px off at its end).
pub fn choice_without_replacement(rng: &mut Pcg64, pop_size: u64, size: usize) -> Vec<u64> {
    const CUTOFF: u64 = 50;
    if pop_size > 10_000 && size as u64 > pop_size / CUTOFF {
        let n = pop_size as usize;
        let mut idx: Vec<u64> = (0..pop_size).collect();
        shuffle_int(rng, n, (n - size).max(1), &mut idx);
        return idx[n - size..].to_vec();
    }
    let mut idx = vec![0u64; size];
    let set_size_seed = (1.2f64 * size as f64) as u64;
    let mask = gen_mask(set_size_seed);
    let set_size = (1 + mask) as usize;
    let mut hash_set = vec![u64::MAX; set_size];

    for j in (pop_size - size as u64)..pop_size {
        let val = rng.bounded(j);
        let mut loc = (val & mask) as usize;
        while hash_set[loc] != u64::MAX && hash_set[loc] != val {
            loc = ((loc as u64 + 1) & mask) as usize;
        }
        let slot = (j + size as u64 - pop_size) as usize;
        if hash_set[loc] == u64::MAX {
            hash_set[loc] = val;
            idx[slot] = val;
        } else {
            let mut loc = (j & mask) as usize;
            while hash_set[loc] != u64::MAX {
                loc = ((loc as u64 + 1) & mask) as usize;
            }
            hash_set[loc] = j;
            idx[slot] = j;
        }
    }
    // `_shuffle_int(size, 1, idx)`
    shuffle_int(rng, size, 1, &mut idx);
    idx
}
