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
  proves it landed.
- `backend/tools/diffcheck.py` — runs a pipeline stage in both implementations
  over the corpus and reports where they disagree
- `backend/bench/` — Vexel Bench (`python -m bench …`); `bench/geometry.py` measures
  against vector truth, `bench/truth.py` reads the truth's corners
- `backend/tests/` — pytest; run `cd backend && .venv/bin/python -m pytest`.
  Rust tests: `cd backend/vexel-rs && cargo test`
- `frontend/` — Studi0Trace React 18 + TS app; `npm run test:run`, `npm run build`
  (`src/components`, `src/hooks`, `src/lib`; tokens in `src/styles.css`)
- `docs/superpowers/specs|plans/` — design specs and implementation plans

## Conventions

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
  reads the SVG (the frontend's `svgdoc.ts`) must resolve `<use>`.
- `refine=True` (off by default) runs `vexel/refine_render.py` after the fit:
  for each node and each interior control point, the two shapes on either
  side are rendered with resvg into an integer-aligned 16 px crop at 4×,
  averaged back to source pixels and compared with the source in a 2 px band
  along the arc; a 0.1 px nudge (a node with every arc that meets it, a
  control point along its normal) is kept when the band error falls by more
  than 0.02 grey levels. Nodes on the frame and wedge tips never move. It needs a renderer,
  so it runs in Python only: `VexelEngine.trace` routes `refine=True` to the
  Python pipeline whatever `VEXEL_BACKEND` says. That is the one place the
  engines differ on purpose.
- After the fit, `vexel/regularity.py` clusters every straight segment's
  direction across the boundary graph and snaps clusters carrying 40 px or more
  to one direction (axis within `snap_axis_deg`, exactly perpendicular to a
  heavier cluster within a degree). Lines turn about their node end or their
  midpoint; nodes never move, so rings still close. A line with a node at both
  ends is left alone.
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
- The Rust engine is not allowed to diverge from the Python one by accident.
  `tools/diffcheck.py` holds the partition's labels to the last float32 bit and
  the fills to a colour level; where the two are allowed to differ, the
  tolerance table says so and says why.
- Python env: `backend/.venv` via `uv`. Docker image: `backend/Dockerfile`.
- Frontend follows the Studi0 design system (semantic HSL tokens, Poppins,
  `.dark` on `<html>`, `h-10 rounded-md` buttons, sticky blurred header). Never
  use a one-sided coloured border as a highlight; use the yellow dot, a
  `ring-1 ring-primary/20`, a muted tint, or a weight shift.
- Frontend tests run in `src/test/env.ts` (jsdom + Node fetch globals) with MSW
  handlers in `src/test/server.ts` that mirror the backend contract.
