# Contributing

Thanks for looking. This is a small project with one strong opinion, described
below — everything else is negotiable.

## The one rule

**A tracing quality change is not an improvement until the bench says so.**

```bash
cd backend
.venv/bin/python -m bench run --engines vexel --label my-change
.venv/bin/python -m bench compare bench/baselines/vexel.json bench/reports/<stamp>-my-change/results.json
```

If a class regresses, tighten the change — do not move the baseline. Baselines
move only when a change is understood, deliberate and explained in the commit
message. Quoting a number in a comment, a preset description or the README
means that number was measured on the current corpus with the current build.

## Setup

```bash
cd backend && uv venv && uv pip install -e '.[dev]'   # Python ≥ 3.12
cd frontend && npm ci                                  # Node ≥ 20
```

Run both servers:

```bash
backend/.venv/bin/python -m uvicorn studi0trace.main:app --app-dir backend --reload
npm --prefix frontend run dev
```

## Before you open a pull request

```bash
cd backend   && .venv/bin/python -m pytest
cd frontend  && npm run test:run && npm run build
```

New behaviour ships with a test. The concurrency and CORS-on-error tests in
`backend/tests/test_api.py` are regression guards for real production
incidents — if one of them fails, something is actually broken.

## Conventions worth knowing

- **Engines are synchronous.** Only `api/` touches asyncio/anyio.
- **Every engine parameter lives in its Pydantic `Params` model**, with bounds,
  a default, a description and `json_schema_extra={"ui": {...}}`. The UI is
  generated from that schema, so adding a parameter needs no frontend change —
  and hardcoding a control in the frontend defeats the point.
- **Errors that reach a client carry a stable `code`.** See `IntakeError` and
  `ErrorBody`.
- **No one-sided coloured borders as highlights** in the UI. Use the brand dot,
  a `ring-1 ring-primary/20`, a muted tint, or a weight shift.

## Adding an engine

Implement the `Engine` protocol in `backend/studi0trace/engines/base.py`, then
register it in `registry.load_builtin()`. It needs an `id`, a `label`, a
`description`, a `Params` model and a `trace()` that returns through
`engines.base.finish()`. Set `primary = True` if it should appear in the app's
own UI; leave it off and the engine is still callable through the API and
usable in the bench, which is where engine comparisons belong.
