# CLAUDE.md

Studi0Trace: raster → SVG tracing service with pluggable engines and the Vexel
fidelity bench. Read `README.md` first — it has the run/test/API reference.

## Layout

- `backend/studi0trace/` — FastAPI app (`main.py`), `api/`, `engines/`, `imaging/`
- `backend/vexel-rs/` — Vexel's pipeline in Rust, built into the `vexel_rs`
  extension module with `maturin develop --release -m vexel-rs/Cargo.toml`.
  This is what runs; `engines/vexel/*.py` is the reference it was ported from
  and the fallback when the extension is missing. `VEXEL_BACKEND=python|rust`
  selects explicitly. **Change one and you change both** — a fix to a stage in
  Python needs the same fix in `vexel-rs/src/`, and `tools/diffcheck.py` is what
  proves it landed. **And a third step** when the change alters what the engine
  writes: the core's tests compare that SVG to the byte on macOS arm64, so
  re-export its fixtures with the Rust `vexel_rs` built from your checkout
  (`cd backend && .venv/bin/python -m tools.export_core_fixtures --only scorecard,api,auto`
  makes the tests pass again — `auto` checks the wordmark's trace against the copy
  `scorecard` keeps, so it cannot run without it; `svg,render,drawing,holes,geometry`
  also hold a trace, as an input, and are worth re-exporting so they stay current) and
  run `cargo test --workspace --release`.
- `crates/studi0trace-core/` — the Rust port of everything the Python server does
  around the engine: image intake, the parameters and their JSON schema, presets,
  SVG finishing, the artifact scorecard, Auto, and the `Core` facade that answers
  the API's five calls with the API's JSON. The desktop app of plan 2
  (`apps/desktop`) calls it; the web build of plan 3 will, and does not exist yet. Its `README.md` has the
  facade, the known differences from the Python and the contract a shell must
  keep. The root `Cargo.toml` is the workspace of it and `backend/vexel-rs`:
  `cargo test --workspace --release`, Rust ≥ 1.88 (1.90 for `apps/desktop`). Its golden fixtures
  (`tests/fixtures`) are exported from the Python by
  `backend/tools/export_core_fixtures.py` (on macOS arm64 with the Rust
  `vexel_rs` built; it writes `provenance.json` and refuses to run elsewhere
  without `--force`), and `tools/diffcheck.py scorecard` holds its scorecard to
  `imaging/quality.py` over the corpus, through the `studi0trace_core` extension
  (`--features python`, built apart from `vexel_rs`:
  `maturin develop --release -m ../crates/studi0trace-core/Cargo.toml --features python`
  from `backend/`; never `--all-features`, which cannot link without maturin).
  The fixtures were exported on macOS arm64: results that go through libm are
  compared to the bit there (`tests/common::exact()`) and to a tolerance
  elsewhere; `STUDI0TRACE_FORCE_TOLERANT=1` runs the tolerant branch on any
  machine. Results that depend on libm but are rounded to f32 or to fixed point
  before they are compared (the Canny stages in `tests/edges.rs`, whose Gaussian
  taps are `f64::exp` stored as f32; the Lanczos digests in `tests/resample.rs`
  and `tests/render.rs`, whose taps are `f64::sin` rounded to 22-bit fixed point;
  the holes digests) are compared to the bit on every platform and are
  practically immune: all 25 Gaussian taps in the fixture are correctly rounded,
  and tiny-skia's one architecture-dependent operation, `recip_fast`, is used only
  by the colour-burn and colour-dodge blend modes. If one of them ever fails on
  another platform, that is where to look.
- `apps/desktop/` — Studi0Trace for Mac (Tauri 2; plan 2, `docs/superpowers/plans/2026-10-02-studi0trace-mac-app.md`).
  `src-tauri` is a workspace member holding `Core` for describing, presets and intake; every trace runs in a
  child process, the app's own binary with `--trace-worker`, one at a time (`queue.rs`), killed to cancel.
  HEIC/HEIF/TIFF and Downscale go through `/usr/bin/sips`. The webview never names a path to write: Rust shows
  the panels and writes (`export.rs`). AI redraw (`src-tauri/src/redraw/`: `geometry`, `rough`, `drift`, `openai`;
  `keychain.rs`) is the app's only network code and the only code that sees the OpenAI key (Keychain service
  `com.studi0.trace.openai`; its calls run off the async runtime): opt-in, the drift check before a redraw is used,
  the store swapping an image's bytes for its redraw and back (`"{id}-original"`). Errors carry stable codes
  (`no_key`, `invalid_key`, `not_allowed`, `quota`, `refused`, `timeout`, `offline`, `bad_reply`, `too_large`; `cancelled`
  is silent, and a cancelled or superseded redraw emits no terminal `redraw-phase`). `STUDI0TRACE_OPENAI_BASE`
  points it at the tests' mock server (`tests/redraw_openai.rs`), the `#[ignore]`d `live_redraw` calls OpenAI by
  hand, and `tests/redraw_boundary.rs` holds the CSP, the worker and the core offline (spec
  `2026-10-06-ai-redraw-design.md`). The Settings window is 520×780.
  `npm run dev` / `npm run build` / `npm run smoke` from `apps/desktop`;
  `cargo test -p studi0trace-desktop --release` includes the worker tests on the real binary. The crate embeds
  `frontend/dist` when it compiles, so `cd frontend && npm run build` comes before any cargo build of the workspace.
- `backend/tools/diffcheck.py` — runs a pipeline stage in both implementations
  over the corpus and reports where they disagree. Most stages feed both sides
  one input (`segments` hands the Python's placed arcs to both fitters and
  expects the same segments back); `trace_labels` and `trace_arcs` run the two engines end to end
  (`VEXEL_DUMP=<dir>` makes either engine write the label map it hands the
  boundary build, the fitted arcs and the stroke decisions — `vexel/dump.py`,
  `vexel-rs/src/dump.rs`, one format) and compare what each actually produced.
  `scorecard` is a per-stage gate like the rest (both scorecards are handed one SVG,
  so it is in the default run) and exits non-zero, with the build command, when
  `studi0trace_core` is not installed
- `backend/bench/` — Vexel Bench (`python -m bench …`); `bench/geometry.py` measures
  against vector truth, `bench/truth.py` reads the truth's corners. Three sets, each
  gated per item by `tools/qloop.sh full`: `bench/corpus`, `bench/heldout` and
  `bench/degraded` (`python -m bench.degraded generate`: vector-truth sources doubled
  nearest-neighbour, sharpened, rendered at 176 px, or (`combo`) doubled and
  sharpened together with ground noise, at the full size —
  the damage real uploads carry, which the clean sets cannot show a fix's benefit on).
  `bench/degraded/` must never get an `__init__.py`: it shares its name with
  `bench/degraded.py`
- `backend/tests/` — pytest; run `cd backend && .venv/bin/python -m pytest`.
  Rust tests: `cargo test --workspace --release` at the root (the engine and the
  core; `cd backend/vexel-rs && cargo test` for the engine alone)
- `frontend/` — Studi0Trace React 18 + TS app; `npm run test:run`, `npm run build`
  (`src/components`, `src/hooks`, `src/lib`; tokens in `src/styles.css`)
- `docs/superpowers/specs|plans/` — design specs and implementation plans

## Conventions

- The frontend talks to its host only through `src/platform` (`native`: Tauri commands and events; `web`: the
  Python server, kept as a development harness). Images live in `src/state/library.ts`, a store with no React
  in it; components read it with `useLibrary`. Never call `invoke` or `fetch` from a component.
- Engines are synchronous. Only `api/` touches asyncio/anyio.
- Every engine parameter lives in its Pydantic `Params` model with bounds,
  default, description and `json_schema_extra={"ui": {...}}`. Never validate
  parameters by hand elsewhere; never hardcode controls in a UI.
- Engines receive RGBA and return through `engines.base.finish()`.
- Errors that reach clients carry a stable `code`; see `IntakeError`, `ErrorBody`.
- New behaviour ships with a test. The concurrency and CORS-on-error tests in
  `tests/test_api.py` are regression guards for real production incidents.
- Any tracing quality change must be run through the bench and compared against
  `backend/bench/baselines/` before it is called an improvement.
- Neighbouring shapes must tile. The boundary is a graph (`vexel/topology.py`):
  an edge between two regions is one arc, placed and fitted once and given to
  both. Never go back to tracing a region's outline on its own — that is what
  left a hairline of backdrop between every pair of shapes. `bench`'s `seam_ppm`
  measures it and `tests/test_vexel_topology.py` holds it at zero.
- A straight edge must come out straight. `curves.fit_stretch` fits every run
  between two breaks lines first: `line_runs` finds straight runs from the
  residuals about their own total-least-squares line (never from a chord
  between two pre-placed corners, which fail when a corner is a third of a
  pixel off), gaps between runs are cubics, and the answer is kept when it
  costs no more segments than the plain curve fit. Chords of a big circle pass
  the residual test one at a time; what keeps them out is that they turn a
  little against each other (`CHORD_TURN`) and that the curve is cheaper. Do
  not loosen `LINE_RMS`/`LINE_P98` or lower `LINE_MIN` without checking a
  circle still comes out a circle and a small round corner stays round.
- Segments are `Line | Cubic | CircArc`. `curves.fit_arc_run` makes a run one
  circular arc (`A r r 0 large sweep x y`) when the circle *through the run's
  two ends* (`circle_through` — the ends stay, a neighbour meets them, and that
  circle is the one a renderer draws) holds it inside `tol`, it subtends 10° or
  more over a 6 px chord, and any pinned end tangent agrees within 3°; an arc
  costs what a cubic costs, so it replaces a cubic and never a line. A corner
  between a line run and a circular piece is placed where the line cuts the
  circle (`corners_from_runs`), never at the crossing with a short chord of the
  circle that happened to pass the straight-run test. `try_rounded_rect` emits `<rect rx>` for four axis-aligned runs
  joined by four tangent quarter circles of one radius (the radius is read per
  vertex from its distances to the two sides, `(u+v)+sqrt(2uv)`, not from a
  circle fit, which the straight vertices at a run's shed ends would bias).
  Anything that parses a path (`bench/geometry._segments`, the test helpers,
  the frontend's `pathAnchors`) has to accept `A`.
- Before the nodes are placed, `vexel/symmetry.py` tests every single-ring
  region (not touching the frame) for rotational order 2–8 and for mirror axes
  (principal directions, their 45° turns, every 15°, snapped to the canvas
  axes within 1.5°): images within 0.10 px mean / 0.30 px at the 99th
  percentile are a real symmetry, and the vertices are replaced by the mean of
  their images. A closed arc with a mirror axis is then fitted on one half and
  reflected (`topology._fit_mirrored`), with a corner on the axis sharpened as
  the approach line's crossing with the axis, so the output is exactly
  symmetric. Repeated shapes (same primitive to 0.1 px, or paths whose
  outlines agree to 0.1 px after translation) are written once into `<defs>`
  and painted as `<use href x y fill>` (`vexel/reuse.py`); anything that
  reads the SVG (the frontend's `svgdoc.ts`) must resolve `<use>`. A shape
  that carries a filter or a gradient is written in full: a filter or a
  `userSpaceOnUse` gradient on a `<use>` applies in the use's own user space,
  which its x and y shift, so a filter region in the file's units moves with
  the copy and clips it, and a gradient solved in the file's units paints the
  copy with the wrong stretch of itself (a cherry 240 px right of its twin).
- A ring that is a circle or a rectangle draws as that primitive, and its
  arcs carry the primitive's outline (`topology._imprint`, after the fit and
  the regularity snap, before the bleed): the nodes move onto it and every arc
  meeting them follows, so the neighbours draw the same curve. Left as the
  arcs' own fits they sat a quarter pixel off a circle and a few hundredths off
  a rect's side, and where the primitive was painted first the two
  anti-aliased edges left a hairline (a ring of seam round a disc, a line
  between every two tiles). A primitive painted before a neighbour keeps its
  bled outline beneath it (`Boundary.bleeds`), as any shape reaches under a
  later one. A node the primitive would move by more than the tolerance
  vetoes the ring; ellipses are not written back.
- `upsample` (default `auto`): an input of at most 192 px whose own direct
  trace has a region thinner than 2.2 px (2·area/perimeter over the label map
  handed to topology) is traced again at 2× through a Lanczos-3 upsample and
  drawn back inside `<g transform="scale(0.5)">` under the original viewBox
  (`vexel/upsample.py`, `vexel-rs/src/upsample.rs`; the tap weights are the
  same literals in both and `tools/diffcheck.py upsample` holds the two images
  to the byte). The rule needs no renderer. Do not apply the upsample blind:
  on large sharp shapes it reads the resampler's ringing as edge position and
  makes them worse (spec `2026-09-23-vexel-small-input-upsampling.md`).
  Anything that reads path coordinates has to honour that root group
  (`bench.geometry.root_scale`, the frontend's `svgdoc.ts`).
- `refine=True` (off by default) runs `vexel/refine_render.py` after the fit:
  for each node and each interior control point, the two shapes on either
  side are rendered with resvg into an integer-aligned 16 px crop at 4×,
  averaged back to source pixels and compared with the source in a 2 px band
  along the arc; a 0.1 px nudge (a node with every arc that meets it, a
  control point along its normal) is kept when the band error falls by more
  than 0.02 grey levels. Nodes on the frame and wedge tips never move. Both
  engines run it: Python renders the two shapes' markup with resvg, Rust draws
  the same geometry with tiny-skia (`vexel-rs/src/refine_render.rs`, the
  rasteriser resvg itself uses), coordinates rounded to the file's precision
  in both; shapes under a blur filter are left out of the crop in both, since
  tiny-skia has no filters. The two agree to a few thousandths of a pixel of
  outline error, not to the bit (tests/test_vexel_refine.py holds them to
  0.005 px on the heart).
- After the fit, `vexel/regularity.py` clusters every straight segment's
  direction across the boundary graph and snaps clusters carrying 40 px or more
  to one direction (axis within `snap_axis_deg`, exactly perpendicular to a
  heavier cluster within a degree). Lines turn about their node end or their
  midpoint; nodes never move, so rings still close. A line with a node at both
  ends is left alone. No snap — this stage's, `_snap_axis`'s in the arc fit or
  `snap_axis_lines`'s on a closed contour — may move a line's end further
  than `SNAP_END_MOVE` (0.15 px, what the placement knows an edge to): a
  side drawn 1.3° off vertical is inside the snap angle, and turning a 330 px
  one onto the axis took 3.6 px off each end of a glyph's bar.
- Junction nodes are placed where the incident arcs' approach lines cross, at
  any angle, and held on the canvas edge; the vertices inside a node's approach
  window are never fitted (`NODE_TRIM`, `TIP_TRIM`, capped at `TRIM_SHARE` of
  the arc). Two arcs continue smoothly through a node only if one line or one
  cubic fits ten pixels of each; the corner threshold does not decide that.
- A hard label map cannot hold a sub-pixel sliver, so an acute wedge arrives at
  `topology` already truncated. `_extend_wedges` hands the sliver back from the
  three-way colour mix, and the tip is fitted as a cusp. Any chain it claims
  must stay **four-connected**: `_directed_rings` breaks a diagonal touch the
  four-connected way, so an eight-connected chain comes back as one-pixel
  islands. `tools/diffcheck.py`'s `wedges` stage compares the extended labels
  and `arcs` is then given one map, so each is tested on the other's output.
- Thin regions are stroked along their medial axis (`vexel/strokes.py`,
  `vexel-rs/src/strokes.rs`). skimage's `medial_axis` thins in an order it
  breaks ties in at random, so the Python runs skimage's algorithm itself
  (`strokes.medial_axis`) with a hash of each pixel's raster index as the
  tiebreaker; `core/skeleton.rs` sorts by the same key. Never break the
  tie by raster index: on a two-pixel line that thins the same side first
  everywhere and puts the centreline half a pixel off, enough for
  `stroke_fidelity` to fail a ring the random order passes. The skeleton is
  only the start: `_refine_centreline` moves every vertex along its normal to
  the bilinear coverage centroid across the stroke (±(w/2+1) px at 0.25 px,
  two passes), and `stroke_fidelity` predicts each pixel's coverage from its
  exact distance to that polyline — never from a rasterised centreline, which
  scored a line half a pixel off the lattice at the gate whichever way the
  tie-break fell. The exact measure runs at two thirds of the old one, and
  the gate (`stroke_tolerance`) is 0.13: over corpus and held-out every drawn
  line scores at most 0.118, the stems and blobs a stroke would mangle 0.141
  up. The centreline is then fitted at `STROKE_FIT_SHARE` (half) of the curve
  tolerance, since its error shows on both edges of the stroke; at the full
  tolerance the cubics through a 150 px ring sagged 0.14 px inside it.
  `tools/diffcheck.py strokes` compares the two per thin group.
- A pixel's stored colour is noise below 8-bit alpha 32 (straight alpha
  quantises it to ±128/alpha levels; a resampled asset rings every edge with
  alpha 1–15 noise), so the transparent field is never inpainted from it as
  it stands: `prepare.inpaint_transparent` reads such a source through
  `settle_rim`, the alpha-weighted mean of its 5×5 neighbourhood (the ink
  beside an edge, a faint field's own colour in a halo), and visible pixels
  keep the colour they have — settling them too moved a shadow's halo to its
  caster's colour. `upsample2x` inpaints the same way before it resamples,
  since its channels are straight and the black under transparency would be
  mixed into every edge at 2×. Inpainted from noise, the field carried seams
  the partition read as edges: a serrated triangle, a ring in 39 fragments.
- The Rust engine is not allowed to diverge from the Python one by accident.
  `tools/diffcheck.py` holds the partition's labels to the last float32 bit and
  the fills to a colour level; where the two are allowed to differ, the
  tolerance table says so and says why. Its default run is the per-stage
  contract and passes on every corpus item; `--all` adds the end-to-end
  `trace_labels`/`trace_arcs` stages, which compare whole pipelines and differ
  wherever an allowed tolerance sits at a decision threshold (a rescue residual
  at 1.0 on a shadow band, a straight-run test on arcs placed to 0.05 px).
  Those are diagnostics: read them to find where a tolerance bites, do not
  gate on them. Nothing in either pipeline may depend
  on an order that is not defined — a Python `set`'s iteration order decided
  which of two equidistant regions a rim pixel joined, and three such pixels
  turned a wedge tip into a hairpin. Ties are broken by something both engines
  compute (`engine.split_rim`: distance, then the pixel's own colour, then the
  lower label). The stroke stage's skeleton is `strokes.medial_axis`, skimage's
  algorithm with a hashed-pixel tiebreak instead of skimage's OS-seeded one
  (see the stroke bullet above), so a trace is reproducible and the two
  engines thin identically (`diffcheck skeleton`). The rescue residual weights a pixel's colour by its alpha: the
  colour under a transparent pixel is inpainted and means nothing, and scoring
  it left whole transparent fields at the rescue threshold. Where two
  candidates tie to the last bit — a staircase puts two vertices at the same
  distance from a cubic — an argmax answers by arithmetic order, which the two
  languages do not share; `curves._max_error` calls anything within
  `SPLIT_TIE` tied and takes the vertex nearest the run's middle. Paint
  order ties go to the lower label (`order.paint_order`): a HashMap's order
  there repainted a mosaic differently on every run. Every other choice that
  can tie to rounding has a named tie and one rule in both engines: a length
  that is a whole number of resampling steps (`STEP_TIE`), symmetry's nearest
  vertex (`NEAR_TIE`, lower index) and a ring too isotropic to have principal
  axes (`ISOTROPIC`, the 15° grid only), ramp knots that fit equally well
  (`RAMP_TIE`, the lower candidate), a two-point cubic whose tangents meet at
  its end (`MEET_TIE`). The Rust subsamples a region's pixels exactly as
  numpy's `Generator.choice` does, on both of its roads (`core/rng.rs`); alpha
  is scaled to 255 in float32 as the Python scales it (`prepare::alpha255`).
  `tests/test_vexel_parity.py` guards each of these.
- The artifact scorecard (`studi0trace/imaging/quality.py`, re-exported by
  `bench/artifacts.py`; `python -m bench artifacts OUT.svg SRC.png --where`)
  counts what a person sees and the averaging metrics hide: pinholes, slivers,
  sub-pixel strokes, wobble, inflections, rectangles with uneven radii or
  bowed sides. It scores only visible outline (a crisp id render decides what
  is on top), so the copies bled under later shapes do not count. It is in
  every `bench run`; a quality change is judged on it as well as on `score`.
- The bled copy an earlier shape draws under a later one is an offset of the
  drawn curve (`topology._under`), stopped half way to any wall behind which
  its colour would show, one copy per arc into the side painted later. A
  stacked shape paints on under the holes that hold only opaque later shapes
  (`engine._holes_to_fill`), a stroked region is filled underneath by its
  earliest neighbour, and overlap tops are graph unions, not coverage traces.
  `diffcheck under` holds the two engines' copies together.
- A region's edge band belongs to its edge. Fills are fitted on the core
  (`weights.interior` depth > 3) and a gradient whose range over the core is
  under 2·tol is the solid; the rescue does not promote an edge's rendering
  (`rescue.edge_mix`); placement reads colour premultiplied, against each
  region's fill plus its own smoothed residual (local fills, gated off where
  the two sides' local colours converge). A shadow on a transparent canvas is
  a filter (`shadows._detect_clear`). The bands a drop-shadow filter explains
  join the ground they lie on before the outline is built, an opaque backdrop
  as much as a transparent canvas (`ShadowPlan.backdrop`): left in the label
  map and skipped at paint time, the rescued band stopped a pixel short of the
  caster and the thread of backdrop between them cut the caster's outline into
  two-point arcs, a node at every step of a rounded corner.
- The colour under a pixel of alpha below `prepare.COLOUR_ALPHA_FLOOR` (8/255)
  may be unpremultiply noise: a premultiplied pipeline leaves it quantised to
  steps of 255/a per channel (at alpha 1 or 2 only 0, 128 and 255), about 45/a
  ΔE of noise against the neighbours over the corpus, and below 8 a halo of it
  is all ridge and no seed (`seed_mask`'s 8 ΔE/px), so the watershed flooded a
  speech balloon out to the far end of its shadow's alpha-2 halo and the
  rescue carved the halo back out as an "invisible" region that took the rim
  with it. A colour on that grid (`unpremultiply_noise`, a level of rounding
  either way) becomes the alpha-weighted mean of the noise pixels within
  `NOISE_RADIUS` (`smooth_faint_noise`, 7×7: alpha-2 noise from ±64 to ±9
  levels), its own neighbourhood's samples and never a shape's — inpainted
  from the nearest pixel that shows, a ramp's tail beside a disc took the
  disc's colour (the corpus is rendered premultiplied: its tails are on the
  grid too). A colour off the grid is a straight-alpha file's own and stays.
  The 2× upsample resamples the prepared colour, not the file's: what a file
  stores under alpha 0 was mixed into the ringing beside every thin line. And
  a four-connected piece of a region below `min_region` is not a region: the
  shards carving a feature leaves of its host — the islands the host's fill
  passes through, the one-pixel thread of the host's own edge between the
  feature and a third region, a sliver each in `_directed_rings` — join the
  nearest neighbouring region as `split_rim` hands a rim over
  (`engine.absorb_shards`: distance, own colour, lower label; never an
  invisible one while a visible one is as near; a label whose every piece is
  small is a dotted line, not shards), after the shadow stage and without
  reading the enclosure again: the shards are the host's edge, and an inset
  shadow's band is enclosed by its card through the card's corner bits — with
  those bits in the band and the enclosure read again, the card was painted
  without its band ring; absorbed before the shadow stage, the band touched
  the backdrop and the shadow model no longer fitted.
- Rounded rectangles are read under blur (`vexel/rects.py`: the blur is taken
  out of each radius, r² ≈ r_read² − (1.86σ)² − 0.58), given one radius per
  shape and across shapes, one size and shared edge levels, and a corner that
  meets a neighbour becomes a cusp; a Line-curve-Line corner elsewhere is one
  circular arc of the mark's radius (`topology._rectify`, `_fillets`;
  `vexel-rs/src/rects.rs`, `topology/rectify.rs`; `diffcheck rects`).
- A radial fill may be SVG's focal radial (`fills.Radial.fx/fy`,
  `focal_param`): a lit sphere's highlight sits off its centre, and the
  concentric fit put its brightest ring in the wrong place. The focal search
  runs only when the concentric radial leaves `FOCAL_MIN_RMS` tolerances of
  error over `FOCAL_MIN_PIXELS`, keeps the focal point within `FOCAL_REACH`
  of the radius (where the parameter is well conditioned) and must win by
  `FOCAL_MARGIN`. Anything that reads a radial's geometry has to honour the
  focal point: a level line is the circle of radius t·r centred at
  F + t·(C − F) (`posterize`), the Rust renderer's start point is F, and the
  diffcheck wire carries `[cx, cy, r, fx, fy]` with F = C when concentric.
- `gradients=False` posterises the fitted model, not the pixels
  (`posterize.posterize_fills`): each ramp is cut at equal-ΔE levels of its own
  parameter, band edges are placed on the level line exactly, and bands are
  never strokes or overlaps; where the model's level lines are not the image's
  the pixels' own settled level lines are used (`diffcheck posterize`).
- A seed area is one region only while it is one colour: `partition` erodes
  each area 3 px, groups what is left by colour (a group needs 64 px), seeds a
  split area by its groups and rejoins neighbouring pieces unless the boundary
  between them is a real step (gradient ≥ 3× either piece's own and ≥ 0.25× their
  colour difference). A weak edge meeting a strong one drops out of the ridge map
  for a few pixels, and one strip of seed across that gap had fused a crater
  into its moon under JPEG, before any later stage could see it. A step is a
  ridge: the split is kept only where the boundary is `NECK_RIDGE` times
  steeper than the pixels `NECK_REACH` to either side at more than half its
  pairs — a boundary through a soft band (the necks of a ring of seed round a
  highlight) is as steep beside itself as on itself and had seeded a cap in
  two halves with a seam through the highlight's ends. And a region is
  an overlap of two shapes only if at least half its outline runs along them
  (`overlaps`): a face read as a translucent eye over a sliver was dropped.
  And a blend is evidence of translucency only when the shift it makes,
  (1 − α)·|backdrop − X|, is `OVERLAP_SHIFT` fit tolerances: a flat tile four
  levels from its neighbours was drawn as the next tile at 96 % over it.
- A crisp edge is a real boundary however small its step. The partition's
  ridge threshold and junction test drop to `G_LOW_MIN`/`G_SEED_MIN` where the
  area is clean outright (the median discontinuity round the pixel, ridges
  left out, under `CLEAN_FLOOR`; `partition.noise_floor`), and the merge
  refuses a gradient across a boundary that stands `EDGE_PROMINENCE` times
  above both regions' interior discontinuity, is at least `EDGE_FLOOR`, and
  is a ridge at more than half its pairs, merging only colours within
  `EDGE_SAME` as solids (`merge.ridge_pairs`, `interior_floor`). Sixteen tiles
  2.9 ΔE apart came out as two shapes before. JPEG's ringing keeps every old
  threshold: its floor is never clean and its interiors are never flat.
- A stretch that is lines-first at `KIND_TOL` (0.4 px) stays lines at any
  looser `curve_tolerance`: a loose tolerance buys fewer curve segments, never
  a straight edge drawn as a bow.
- A soft edge has no position finer than its blur. `_crossing` also returns
  how far coverage drops across the three pixels it samples (1.0 on a crisp
  edge); `_place` reads the blur width off the chain's median drop
  (`SOFT_WIDTH / drop − SOFT_BIAS`) and smooths the placed vertices along the
  arc by a Gaussian of that width (from 1 px, capped at 4, never a chain
  shorter than the kernel, never a posterised level line), in both engines
  (`_soften`, `soften`; `diffcheck placed`). Without it every vertex of a
  ramp fell to the lattice edge and the fit drew the label staircase as a
  wobble. Two other things make a soft band wobble and are fixed at their
  source, not here: `refine_merge`'s edge veto holds only on a *ridge*
  (`merge.boundary_ridges`, the partition's `NECK_RIDGE` test — a glow's own
  slope is not a step), and a rescued band takes its parent's edge band up to
  the outline (`engine.reach_the_edge`), or a strip of the parent runs on
  between them and puts a node at every row of a diagonal.
- Presets are measured, never described by hand: `bench.presets_eval
  --write-details` rewrites `engines/preset_details.json`. Auto (`auto.py`,
  `/vectorize auto=true`) traces the candidates concurrently and keeps the
  cleanest within the fidelity slack of the most faithful.
- The core is a port, not a fork, and stays wasm-clean: no filesystem, no
  `Instant` outside `auto::trace_finished` (the core's one clock, which plan 3 must
  hand a wasm clock; the engine's `vexel-rs/src/timing.rs` `Timer` reads one only
  when `VEXEL_TIMING` is set, and must stay that way), no threads
  outside `rayon` in `auto.rs`, no C dependencies. Change the Python scorecard, Auto, intake, presets or API
  and you change the core; `export_core_fixtures` regenerates what its tests hold
  and `diffcheck scorecard` (a default stage) proves the scorecard end to end.
  The preset bundles are `engines/presets.json`, one file both read, and
  `preset_details.json` is embedded in the core too: `presets_eval --write-details`
  changes what the core answers, so re-export the fixtures after it.
- Python env: `backend/.venv` via `uv`. Docker image: `backend/Dockerfile`, which builds vexel-rs alone
  (context `backend/`, no workspace): it reads `backend/vexel-rs/Cargo.lock`, never touched by cargo inside
  the workspace (`--locked`), and carries the root `[profile.release]` as `CARGO_PROFILE_RELEASE_*`
  variables. Both are held by `tests/test_vexel_lock_matches_workspace.py`; after the workspace's
  dependencies move, `cd backend && .venv/bin/python -m tools.sync_vexel_lock` refreshes the lock.
- Frontend follows the Studi0 design system (semantic HSL tokens, Poppins,
  `.dark` on `<html>`, `h-10 rounded-md` buttons, sticky blurred header). Never
  use a one-sided coloured border as a highlight; use the yellow dot, a
  `ring-1 ring-primary/20`, a muted tint, or a weight shift.
- Frontend tests run in `src/test/env.ts` (jsdom + Node fetch globals) with MSW
  handlers in `src/test/server.ts` that mirror the backend contract.
