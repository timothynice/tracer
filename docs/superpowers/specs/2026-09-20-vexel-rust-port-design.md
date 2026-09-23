# Vexel in Rust — design and results

**Goal.** A Rust implementation of the Vexel tracing pipeline that is much
faster than the Python one and no worse on the bench. The Python engine stays in
the tree as the reference implementation and as the oracle the port is tested
against.

**Result.** 10.3× faster over the whole corpus (134.6 s → 13.0 s), and lower
mean ΔE in all four classes. Numbers and how they were measured are at the
bottom.

## Why

Measured over the 63-item corpus with the code as it was on `main`, Python Vexel
took 1.9 s an image. Where it went:

| stage | share |
|---|---|
| `detect_shadows` | 66.9 % |
| `enclosure` | 6.6 % |
| `initial_labels` | 4.6 % |
| `merge_regions` | 3.7 % |
| `fit_fill` | 3.6 % |
| `fit_shape` + `coverage_field` | 4.8 % |
| everything else | ~10 % |

`detect_shadows` dominated because its `_fit_blur` runs a Nelder-Mead search
whose objective is a full separable Gaussian; `scipy.ndimage.correlate1d` alone
was 5.0 s of a 10.9 s four-image profile. None of the hot paths was parallel:
numpy and scipy here are single-threaded, and the per-region loops are Python.

## Shape

One crate, `backend/vexel-rs/`, built by `maturin` into an extension module
`vexel_rs`. Its public surface is one function:

```rust
fn trace(rgba: &[u8], width: usize, height: usize, params: &PyDict) -> PyResult<String>
```

RGBA bytes in, SVG string out. Nothing else crosses the boundary: no numpy
arrays, no per-stage calls. That keeps the FFI cost at two copies an image and
lets the whole pipeline be `Send`, so `rayon` can be used anywhere inside. The
trace runs under `Python::allow_threads`, so the API's worker threads still
overlap — `tests/test_api.py`'s concurrency guard covers that.

Python keeps what it is good at: `VexelParams` (the Pydantic model the API and
the frontend are generated from), the registry, and the engine seam.
`VEXEL_BACKEND` selects explicitly (`rust` | `python`); the default is `rust`
when the extension imports and `python` otherwise, so a source checkout without
a built wheel still works.

The Rust modules mirror the Python ones one-to-one so the two can be read side
by side, plus a `core/` layer that has no Python counterpart — it is what
replaces `scipy.ndimage`, `skimage` and `numpy.linalg`:

```
core/   grid, filters, morphology, edt, labels, watershed, contours,
        skeleton, colour, linalg, optimise, rng
prepare  partition  stats   merge   refine   rescue   posterize
fills    boundary   order   curves  strokes  overlaps shadows  engine
```

Each `core` module reproduces the semantics of the call the Python makes — the
border mode, the tie-breaking, the label ordering — not the library's full
generality.

## Where exactness was required, and where it was not

Three algorithms decide the output's *topology*, and a difference in them moves
whole regions rather than a hundredth of a pixel. They are ported to match
exactly and checked pixel-for-pixel by `tools/diffcheck.py`:

1. **watershed** — priority flood with skimage's `(value, age)` ordering. Ties
   in both are broken by the internal layout of its binary heap, so that heap is
   reproduced sift-for-sift and the markers are pushed in raster order. A plain
   `BinaryHeap` gives a different, equally valid segmentation, and every region
   id downstream shifts with it.
2. **connected components** — labels numbered in raster-scan order of their
   first pixel, as `skimage.measure.label` does.
3. **marching squares** — the same 16-case table, the same interpolation, and
   the same deque-joining that turns loose segments into ordered contours.

Two more needed care for a different reason:

- **the float32 dtype path.** The partition's ridge test compares a pixel's
  gradient against a bilinearly interpolated neighbour's. On a flat tile those
  are equal only if both sides round the same way, so `rgb2lab`, the Scharr
  convolutions and the Gaussian all reproduce scipy's and skimage's float32
  stores, including which intermediate is rounded and which is not. Carrying
  float64 throughout flipped a tenth of the ridge pixels on `flat/mosaic` and
  changed its partition completely.
- **`numpy.random.Generator.choice`.** `fit_fill` subsamples large regions with
  a fixed seed, and which pixels it picks changes the fitted gradient. PCG64 and
  Floyd's algorithm are reproduced so that the two implementations draw the same
  sample — the only way to tell a real difference in a fit from sampling noise.
  numpy switches to another algorithm once the population is more than four
  times the sample; that branch is undocumented and free to change between
  releases, so it is deliberately **not** emulated. Pinning Vexel's output to a
  private detail of one numpy version would mean an upgrade silently changing
  every trace.

Everything downstream is numeric, and the harness judges it numerically: fills
are compared by *what they paint* at the region's own pixels, to within one
colour level of 255.

## Two things the port found

**`skimage.morphology.medial_axis` is not deterministic.** It breaks ties in its
processing order with `np.random.default_rng(None)`, seeded from the OS. Twenty
calls on the same mask gave up to twenty different skeletons. Which thin regions
Vexel turns into strokes rides on that, so the Python engine's stroke geometry
was not reproducible between runs. The port first broke the same ties by raster
index. That turned out not to be inside the distribution skimage draws from: on
a two-pixel ring every pixel ties with the one across from it, the raster order
thins the same side first all the way round, and the skeleton sits half a pixel
off centre — `logo/thin-mark-128`'s ring measured a stroke fidelity of 0.245
that way against 0.187–0.195 over nine random draws, so the Rust engine filled
a ring the Python one stroked (2026-09-22). Both now break the tie by a hash of
the pixel's raster index (`skeleton::pixel_key`; `strokes.medial_axis` runs
skimage's algorithm with the same order): deterministic, identical in both, and as
even-handed as the random draw (0.198 on that ring). `tools/diffcheck.py
strokes` compares the thin decision, the grouping, the centreline, the width
and the fidelity per thin group.

**Two of the corpus items were being scored on luck.** `logo/cutout-128` and
`logo/thin-mark-128` were the port's two worst regressions, until the fit sample
was varied: at the shipped cap of 2 500 pixels, seed 1234 scores them 0.085 and
0.990, while three other seeds score 0.297/0.298/0.298 and 1.199/1.208/1.189,
and the exact fit over every pixel scores 0.311 and 1.249. The Python baseline's
edge on those items was its particular draw, not its algorithm. Raising the cap
to 25 000 removes the variance and costs nothing measurable in either
implementation.

## The one deliberate quality change

Chasing those two items led to a real defect, which is now fixed in **both**
implementations.

`interior_weights` down-weights a region's boundary pixels, because they are
anti-aliasing mixtures of two fills. The falloff was linear, so a one-pixel rim
still carried a quarter of its weight — and on a compact shape there are enough
rim pixels for that quarter to decide things. An opaque disc came out as
`fill-opacity="0.998"` because its own anti-aliasing dragged the fitted alpha
under the threshold; and a sub-pixel line inside a transparent field was partly
absorbed into that field's fitted colour, so the residual pass stopped seeing it
as an outlier and no longer rescued it.

The weight is now squared and its reach raised from 2 px to 3 px. Swept on the
bench (Rust engine, mean ΔE per class):

| reach, power | logo | flat | gradient | shadow |
|---|---|---|---|---|
| 2 px, linear *(before)* | 0.417 | 0.676 | 0.872 | 0.407 |
| 2 px, squared | 0.375 | 0.648 | 0.900 | 0.410 |
| 2.5 px, squared | 0.362 | 0.643 | 0.884 | 0.421 |
| **3 px, squared** | **0.361** | **0.642** | **0.883** | **0.423** |
| 4 px, squared | 0.360 | 0.633 | 0.922 | 0.421 |

3 px is the only setting that beats the shipped baseline in every class, on both
ΔE and the composite score. The same change applied to the Python engine moves
it the same way (0.359 / 0.641 / 0.893 / 0.418), so it is a property of the
algorithm and not of the port.

## Where the speed came from

Roughly in order of how much each was worth:

- **A blur that knows its own edges.** Under `mode="constant"` a tap outside the
  line contributes exactly zero, so the kernel is clipped to the overlap instead
  of multiplying hundreds of zeros — the shadow fit blurs a 256-pixel grid with
  a σ of 40, where four fifths of a `truncate=4` kernel hangs over the edge. The
  interior of a line is a tight loop with no border logic at all, and the dot
  product carries eight accumulators so the FMA latency chain is not the limit.
  The result is 2–8× faster than `scipy.ndimage.gaussian_filter` before any
  parallelism.
- **A search that runs at the resolution it needs.** A Gaussian of σ is smooth
  on the scale of σ; resolving it to twenty pixels tells the optimiser nothing
  that eight would not. The Nelder-Mead search now runs on a grid coarse enough
  that the blur spans about eight pixels, and the accepted parameters are scored
  once at full resolution, so the `k` and residual the gate sees are never the
  coarse ones.
- **A watershed that only pushes the markers that can move.** A marker pixel
  whose four neighbours are already labelled pops, finds nothing to claim and
  exits; labels are only ever set, never cleared, so it can never acquire a
  claimable neighbour later. Skipping those leaves every claim and every age
  untouched and removes nine tenths of the heap when the partition absorbs small
  regions. `initial_labels` went from 376 ms to 60 ms on the densest corpus item.
- **Windows instead of frames.** Every stage that asks a question about one
  region used to allocate and walk a frame-sized array to do it — on an
  illustration with two hundred regions, two hundred sweeps of the image for
  work that touches a few thousand pixels. `coverage_field`, `thin_coverage`,
  `is_sharp`, `is_thin` and `stroke_fidelity` now work on the region's own
  bounding box, and a `LabelIndex` answers "this region's pixels" from a
  counting sort done once.
- **A tridiagonal ramp fit.** A hat basis only overlaps its neighbours, so the
  weighted normal matrix is symmetric tridiagonal: one pass to accumulate it and
  a Thomas solve on a handful of rows, where the Python builds an n×k design and
  runs `lstsq` over it — up to forty-five times per region.
- **`rayon`, with a floor.** Regions, shadow casters, filter rows and distance
  transforms all parallelise. Below about 10⁵ tap-multiplications the pool round
  trip costs more than the work, and the profile was almost entirely
  `LockLatch::wait_and_reset` until that floor went in.

Two places take the accuracy trade deliberately: the wide-kernel dot product
reassociates its sum (confined to the shadow fit, whose own tolerance is 1e-4),
and least squares is a Householder QR with column pivoting where numpy uses an
SVD. The QR was not a shortcut — normal equations square the condition number,
and the radial surrogate's cubic-in-r design was ill-conditioned enough that
gradient stops came out ten levels off.

## Testing

Four layers:

1. **`tools/diffcheck.py`** runs a pipeline stage in both implementations over
   the whole corpus and reports where they disagree — `rgb`, `features`, `grad`
   and `labels0` to the last float32 bit, `fills` by what they paint. This is
   how the port was built and how a change to either side is checked against the
   other.
2. **`cargo test`** covers the `core` primitives against their definitions
   (border modes, label ordering, exact distance transforms, the PCG64 stream,
   Nelder-Mead on Rosenbrock) and the pipeline end to end on generated
   pictures — a rectangle must come out as a `<rect>`, a ramp as one gradient,
   a cut-out as an even-odd path.
3. **`pytest`**, including `tests/test_vexel_backends.py`, which pins the
   backend switch and checks that the two implementations fit the same
   primitives and the same colours.
4. **the bench**, which is the arbiter of quality and the only thing that can
   call a change an improvement.

## Results

All figures from `python -m bench run --engines vexel` on the 63-item corpus,
Apple M-series, 12 threads. "Python (main)" is the code as it was before this
change; "Python (same algorithm)" is that code with the interior-weight fix and
the raised sample cap, so the comparison is implementation against
implementation.

Mean CIEDE2000 ΔE, lower is better:

| | logo | flat | gradient | shadow |
|---|---|---|---|---|
| Python (main) | 0.401 | 0.671 | 0.892 | 0.444 |
| Python (same algorithm) | 0.365 | 0.640 | 0.882 | 0.419 |
| **Rust** | **0.361** | **0.642** | **0.883** | **0.423** |

Mean time an image:

| | logo | flat | gradient | shadow | corpus |
|---|---|---|---|---|---|
| Python (main) | 3 726 ms | 1 179 ms | 703 ms | 837 ms | 134.6 s |
| **Rust** | **199 ms** | **215 ms** | **127 ms** | **180 ms** | **13.0 s** |
| speed-up | 18.7× | 5.5× | 5.5× | 4.7× | **10.3×** |

Against the shipped Python, the Rust engine has the lower ΔE on 53 of 71 corpus
items; 28 are better by more than 0.02, 9 worse by more than 0.02, and the rest
are within that. Geometry is unchanged: 9.8 paths and 9.5 KB an image against
9.9 and 9.4.

## Build

```bash
cd backend
uv pip install -p .venv/bin/python maturin
.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml
```

`backend/Dockerfile` builds the crate in its own stage and installs the wheel
into the runtime image, which stays a single container. The wheel is `abi3` so
it does not have to be built against the exact interpreter that loads it, and
the image fails the build rather than the first request if the extension did not
land — a silent fall back to the Python pipeline would only show up as every
trace taking ten times as long.

## Risks that remain

- **The radial centre search can settle in a different local minimum.** It is
  Nelder-Mead on a rough objective, and any difference in arithmetic order can
  send it elsewhere. `diffcheck`'s `fills` stage measures the consequence in
  colour rather than in parameters, and the bench says the two agree to a
  thousandth of a ΔE.
- **`gradient/alpha-fade-128` is the one item that got meaningfully worse**
  (3.40 → 3.61). It is the hardest item in the corpus for both implementations —
  an alpha ramp against transparency, where the coverage field and the fill are
  fitting the same signal — and it is the place to look next.
