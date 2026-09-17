# CLAUDE.md

Studi0Trace: raster → SVG tracing service with pluggable engines and the Vexel
fidelity bench. Read `README.md` first — it has the run/test/API reference.

## Layout

- `backend/studi0trace/` — FastAPI app (`main.py`), `api/`, `engines/`, `imaging/`
- `backend/bench/` — Vexel Bench (`python -m bench …`)
- `backend/tests/` — pytest; run `cd backend && .venv/bin/python -m pytest`
- `frontend/` — Vue 3 app (legacy; being replaced by the Studi0Trace React UI)
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
- Python env: `backend/.venv` via `uv`. Docker image: `backend/Dockerfile`.
