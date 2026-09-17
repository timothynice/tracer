# Studi0Trace Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Vue app with a React/TS Studi0Trace workspace whose controls are generated from `GET /engines`, backed by an upload cache so tuning is instant.

**Architecture:** Vite + React 18 + TS. `lib/api.ts` (typed fetch, AbortController), `lib/theme.ts`, `hooks/useHealth`, `hooks/useUpload`, `hooks/useVectorize` (TanStack Query + debounce). Components: `Header`, `Dropzone`, `EngineTabs`, `ParamPanel`/`ParamControl`, `Canvas` (+ `SplitView`, `OverlayView`, `SideBySide`), `StatsStrip`, `Actions`. Backend gains `/uploads` and `image_id`.

**Tech Stack:** react, react-dom, @tanstack/react-query, @radix-ui/react-{tabs,slider,select,switch,tooltip,dialog}, lucide-react, sonner, @fontsource/poppins, tailwindcss 3, vitest, @testing-library/react, msw, jsdom.

Spec: `docs/superpowers/specs/2026-09-16-studi0trace-frontend-design.md`

## Global Constraints

- Tokens exactly as the Studi0 design doc (light/dark HSL tables); `.dark` on `<html>`; `--radius: 0.5rem`.
- No partial-edge accent borders. Active = yellow dot / ring-primary/20 / muted tint / weight.
- Buttons `h-10 rounded-md`; icon buttons 40×40; every interactive element has a visible focus ring and a label.
- All parameter UI comes from schema (`ui.control`, `ui.group`, `ui.label`, `ui.step`, `ui.unit`, `minimum`, `maximum`, `enum`, `default`, `description`). No engine names hardcoded except the samples row.
- Debounce 250 ms; in-flight requests are aborted on new input.
- `VITE_API_URL` default `http://localhost:8000`.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

---

### Task 1: Backend upload cache + `image_id`
**Files:** `backend/studi0trace/imaging/cache.py` (new: `UploadCache(max_bytes, ttl_s)` with `put(TraceInput)->str`, `get(id)->TraceInput|None`, LRU + TTL), `api/routes.py` (`POST /uploads`; `/vectorize` accepts `image_id: str = Form("")`; remove `vectorized`, `selected_method`), `api/schemas.py` (`UploadResponse`, drop legacy field), `settings.py` (`max_upload_cache_bytes`, `upload_ttl_seconds`), `tests/test_api.py`, new `tests/test_cache.py`.
- [ ] Tests: put/get roundtrip; LRU evicts oldest when over budget; TTL expiry; `/uploads` returns id+dims; `/vectorize` with `image_id` works; expired → 404 `image_expired`; neither `file` nor `image_id` → 400; `vectorized` key absent.
- [ ] Implement; full suite green; commit `feat(api): upload cache and image_id for instant re-tracing; drop legacy fields`.

### Task 2: Frontend scaffold + design tokens + brand
**Files:** delete `frontend/src/*`, `frontend/public/*`, `frontend/index.html`, `frontend/vite.config.js`, `frontend/vitest.config.js`, `frontend/run_tests.js`, `frontend/tests/`; create `package.json`, `tsconfig.json`, `vite.config.ts`, `tailwind.config.ts`, `postcss.config.js`, `index.html`, `src/main.tsx`, `src/App.tsx` (placeholder), `src/styles.css` (tokens + base), `src/lib/theme.ts`, `src/test/setup.ts`, `public/brand/studi0trace-mark.svg`, `public/icons/icon-{192,512}.png`, `public/favicon.svg`, `public/manifest.webmanifest`, `.env.local`, `.env.production`.
- [ ] `npm install`; `npm run build` passes; `npm run test:run` passes a smoke test that renders `<App/>`.
- [ ] Commit `feat(frontend): Studi0Trace scaffold with Studi0 tokens, Poppins, brand mark`.

### Task 3: API client + health + upload hooks
**Files:** `src/lib/api.ts` (`getHealth`, `getEngines`, `uploadImage(file, signal)`, `vectorize({imageId, engines, parameters}, signal)`, `ApiError{code,message,status}`), `src/hooks/useHealth.ts` (poll until ok; exposes `status: 'checking'|'waking'|'ok'|'down'`), `src/hooks/useUpload.ts` (hash file, upload, keep `{imageId, width, height, previewUrl, file}`; re-upload on demand), tests with MSW: `api.test.ts`, `useHealth.test.tsx`.
- [ ] Commit `feat(frontend): typed API client, health polling, upload hook`.

### Task 4: Header, Dropzone, empty state
**Files:** `src/components/Header.tsx`, `src/components/StatusPill.tsx`, `src/components/ThemeToggle.tsx`, `src/components/Dropzone.tsx` (drop / click / paste; validates type+size; toasts), `src/components/Samples.tsx` (4 PNGs copied from `backend/bench/corpus/synthetic/*-128.png` into `public/samples/`), `src/App.tsx` state machine (empty ↔ workspace), tests `Dropzone.test.tsx`.
- [ ] Commit `feat(frontend): header, theme toggle, dropzone with paste and samples`.

### Task 5: Engine tabs + schema-driven params
**Files:** `src/lib/schema.ts` (`ParamSpec` from JSON Schema property; `groupParams(engine)`), `src/components/EngineTabs.tsx`, `src/components/ParamControl.tsx`, `src/components/ParamPanel.tsx` (groups, reset, localStorage persistence), `src/hooks/useParams.ts`, tests `schema.test.ts`, `ParamControl.test.tsx`, `EngineTabs.test.tsx`.
- [ ] Commit `feat(frontend): engine tabs and schema-driven parameter controls`.

### Task 6: Vectorize hook + Canvas
**Files:** `src/hooks/useVectorize.ts` (debounce, abort, react-query, `image_expired` retry), `src/components/Canvas.tsx` (view-mode tabs, zoom/pan transform shared by both layers, checkerboard, updating overlay), `src/components/views/{SplitView,SideBySide,OverlayView,VectorView}.tsx`, `src/components/StatsStrip.tsx`, `src/components/Actions.tsx` (download SVG/PNG, copy), `src/lib/format.ts` (`formatBytes`, `formatMs`), `src/lib/raster.ts` (SVG→PNG via canvas), tests `useVectorize.test.tsx`, `format.test.ts`, `Canvas.test.tsx`.
- [ ] Commit `feat(frontend): canvas with split/side/overlay/vector views, stats, downloads`.

### Task 7: Compare-all, responsive, docs, browser gate
**Files:** `src/components/CompareTable.tsx`, responsive classes, `README.md`, `CLAUDE.md`, `render.yaml` check, `.claude/launch.json` unchanged.
- [ ] Browser gate per spec; screenshots light/dark/375 px. Commit `feat(frontend): compare-all table, responsive layout; docs`.
