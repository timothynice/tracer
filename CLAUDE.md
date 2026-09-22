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
- `backend/bench/` — Vexel Bench (`python -m bench …`)
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
