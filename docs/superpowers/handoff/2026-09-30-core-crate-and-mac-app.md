# Handoff: from the quality checkpoint to the Rust core and the Mac app

Written 2026-09-30 at the end of a long quality campaign. Read this first in a fresh chat.

## Where things stand

- Branch `claude/vectorizer-quality-pass-29e3d1` (worktree
  `.claude/worktrees/vectorizer-quality-pass-29e3d1`) is the line to build on. It is **56 commits
  ahead of `main` and not merged**. `main` deploys to Render, so nothing goes to `main` unless the
  owner asks. `claude/q3-merge` is the same tree.
- The engine quality checkpoint is reached and benched (numbers below). Both engines (Python
  reference and Rust `backend/vexel-rs`) agree: `tools/diffcheck.py` passes on all 96 corpus
  items, `pytest` passes on both backends, `cargo test` passes.
- The published article (Inside Vexel, artifact v4) and the LinkedIn kit (`linkedin-kit-v4.zip`)
  are built from the final records. They state the losses as well as the wins.

| Development set (104 images), same scorecard | before | after |
|---|---|---|
| mean colour error ΔE | 0.415 | 0.249 |
| mean outline error px | 0.344 | 0.236 |
| artifact index | 12.9 | 6.3 |
| wobble deg/100 px | 44.9 | 19.0 |

Against VTracer (Auto preset): composite score 119/120 held-out wins and 102/104 development
wins. It still loses on seams (944 vs 96 ppm on held-out, a one-sided coverage metric), wobble
(58 of 120 images) and artifact index (25 of 120). See "Parked quality work" below.

## The plan that comes next

The design is `docs/superpowers/specs/2026-09-24-studi0trace-local-app-design.md`. In short:
Studi0Trace becomes a free MIT Mac app (Tauri 2) whose engine is the Rust core, with the web
version kept as the same core compiled to WebAssembly. Nothing Python ships; Python stays as
developer tooling (reference engine, bench, diffcheck). Four plans (distribution is unsigned, via GitHub Releases):

1. **Core crate** — `docs/superpowers/plans/2026-09-24-studi0trace-core-crate.md` (15 tasks).
   **Done on branch `claude/core-crate-mac-app-handoff-393d71`** (not merged): a Cargo workspace
   with `backend/vexel-rs` and `crates/studi0trace-core`, which ports everything the Python server
   does around the engine (intake, params and schema, presets, SVG finishing, the scorecard, Auto)
   behind one `Core` facade, held to the Python by golden fixtures and by `tools.diffcheck
   scorecard`. Where the implementation differs from the plan, read the plan's "As built"
   section; what a shell (plans 2 and 3) must do around `Core` is in
   `crates/studi0trace-core/README.md` (the "What a shell must do" list: pixel cap, a worker
   thread, the wasm clocks).
2. **Desktop app** — Tauri 2 in `apps/desktop`; not yet written as a plan.
3. **Web on WebAssembly** — `crates/studi0trace-wasm`; not yet written as a plan.
4. **Release and retirement** — GitHub Releases (unsigned, no notarization), removal of the FastAPI
   deploy; not yet written as a plan.

**Plan 1 is done; start with plan 2 or 3** (both call the core; read the crate's `README.md`
first). What follows is the note this handoff carried before plan 1 was executed, kept because
its method still applies to plans 2 and 3: the ports a plan lists must be checked against the
current code before it is executed. For plan 1, `imaging/` (intake, quality, the scorecard),
`auto.py` and `engines/base.py` were unchanged since the plan, and two things had moved:

- **Fixed in the plan already:** `stroke_tolerance` now defaults to 0.13 (was 0.2) in both
  engines. The plan's parameter table carried 0.2.
- **Regenerate, do not copy:** `engines/preset_details.json` was rewritten by the quality pass.
  Task 3 embeds it, so export fresh golden fixtures (`python -m tools.export_core_fixtures`) and
  do not reuse numbers from the plan text.
- The plan's commit trailer says Claude Opus 5.5; use the trailer your session is given.

How plan 1 was executed: `superpowers:subagent-driven-development`, one task at a time, each
ending with `cargo test -p studi0trace-core` green and a commit; plans 2 to 4 can be run the
same way.

## Toolchain on this Mac (checked 2026-09-30)

- Rust 1.98.1 with only the `aarch64-apple-darwin` target. Plan 3 needs
  `rustup target add wasm32-unknown-unknown`; a universal app (plan 4) needs
  `x86_64-apple-darwin`.
- `cargo tauri` is **not installed** (`cargo install tauri-cli --locked` for plan 2).
- Node 26.10.0, full Xcode at `/Applications/Xcode.app`.
- Code signing: none needed. Decided 2026-09-30: the app is open source, distributed through GitHub
  Releases (not the App Store) and is not signed or notarized, so the missing Developer ID
  Application certificate is not a blocker. Plan 4 drops signing, notarization and stapling and
  documents how to open an unsigned app instead.
- Open questions from the design, still open: app name and bundle identifier, minimum macOS
  version, Intel or universal, and where the static web version is hosted.

## How to run things (unchanged, from `CLAUDE.md` and `README.md`)

```
cd backend
uv venv .venv && uv pip install -p .venv/bin/python -e '.[dev]'     # a new worktree needs its own venv
.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml  # install maturin into the venv first
.venv/bin/python -m pytest
.venv/bin/python -m tools.diffcheck                                  # default stages, ~30 min
.venv/bin/python -m bench run --engines vexel --no-media --workers 4 --label X --out DIR
```

## Standing rules

- Never push or merge to `main` unless asked. Render deploys from it.
- A fix to the Python engine needs the same fix in `vexel-rs/src/`; `tools/diffcheck.py` proves it.
- Every quality change is benched against `backend/bench/baselines/`. The baseline and the preset
  lines were rewritten at the quality checkpoint (commit `b167282`).
- One scorecard yardstick for every engine. The competitor records have no SVGs to re-score, so
  do not change `imaging/quality.py`'s visibility rule without re-running all 28 competitor
  configs.
- `rm -rf` is denied by the owner's permission setup. Use `git worktree remove` or ask.

## Where the evidence lives (gitignored; do not lose it)

`backend/bench/reports/keep-2026-09-29/` inside the session worktree (114 MB):

- `final/{heldout,corpus}/records.jsonl` — Vexel Auto on the final engine.
- `competitors/{heldout,heldout-it,corpus,corpus-it}/records.jsonl` — all 28 competitor
  configurations on both sets (VTracer, ImageTracer.js, AutoTrace).
- `art/` — the article and LinkedIn pipeline: `article_numbers.py`, `build_article.py`,
  `linkedin.py`, `figures.py`, `cover.py`, `export.mjs`, `style.css`, the built `site/`, and
  `linkedin-kit-v4.zip`. Every number in both documents is computed from the records.
- Regenerating the materials: `figures.py OUT` (run from the merge worktree's `backend/` with
  `IMAGETRACER_DIR=$HOME/node_modules/imagetracerjs`), then `article_numbers.py`, `build_article.py`,
  `linkedin.py`, then `node export.mjs ABSOLUTE/path/to/site/inside-vexel.html OUTDIR light`.
- Published article: https://claude.ai/artifact/RAYuvuxqgaZCZsEryvctaJ (private, version 4).

## Parked quality work (only after the app is running, or if the owner asks)

- Wobble on real emoji: the wheelchair (329 deg/100 px vs VTracer's 35), the baby, the spiral
  notepad, `u1f61b`, `u1f4a0`. Hard-edge junction geometry and small specks on soft shading.
- Seams metric: `bench/metrics.seam_index` counts only under-coverage of the source's alpha, so an
  unbiased edge scores worse than an outward-biased one. Decide whether to change the metric
  (for every engine) rather than bias the output.
- Pinholes on the wheelchair; Auto picks by proxies that cannot see the truth outline.
- Downsampled tiles still merge; ellipses are not written back as primitives.
- Mosaic-128 artifact rose 3.5 to 5.6 (the jog a bled copy makes at a tile corner); the
  low-contrast tiles are now separate shapes, correctly.

## Housekeeping left behind

- Worktrees: `q3-merge` and three `agent-*` worktrees with their branches (`claude/q3-*`,
  `worktree-agent-*`) are merged and can be removed with `git worktree remove`.
- A stray `~/node_modules/imagetracerjs` install (used by the competitor runs; safe to keep).
