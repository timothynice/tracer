# Studi0Trace Frontend (Project C)

**Date:** 2026-09-16
**Status:** Written under Tim's instruction to proceed autonomously after Projects A+B. Depends on the A+B spec (`2026-09-16-foundation-and-vexel-bench-design.md`).

## Goal

Replace the 1,731-line Vue `App.vue` with a React + TypeScript app on the Studi0
design system, rebranded **Studi0Trace**, that renders its parameter controls
from `GET /engines`, feels instant while tuning, and is ready for Vexel to
appear as a third engine with zero frontend changes.

## Design system source

- Tokens, type scale, radius, shadows, component recipes: `~/Development/studi0flow-1/design.md` (the written Studi0 system) and `~/Development/studi0mail/ui/src/styles.css` (the living implementation).
- Stack matches the other Studi0 web apps: React 18, TypeScript, Vite, Tailwind 3 with semantic HSL tokens, `.dark` class theming, Radix primitives, lucide-react icons, TanStack Query, sonner toasts, `@fontsource/poppins`.
- Identity: slate primary (`215 20% 30%` / dark `215 20% 45%`), yellow `primary-foreground` (`58 100% 74%`) used sparingly as the accent, `--radius: 0.5rem`, restrained shadows, sticky `border-b bg-background/95 backdrop-blur` header.
- Deviation, per Tim's global UI rule: no partial-edge accent strokes (the `inset 3px 0` active-nav treatment in studi0mail). Active states here use the yellow dot / `ring-1 ring-primary/20` / muted tint / weight shift.

## Information architecture

Single workspace, no router. Two states:

1. **Empty** — the header plus a large dashed drop card: "Drop an image, paste, or browse" · PNG JPG GIF WEBP BMP · 20 MB. A row of four sample images (from the bench corpus) for instant try-out.
2. **Workspace** — header · canvas (left, fluid) · controls (right, 336 px; stacks below on < 1024 px).

### Header (sticky, blurred)

Brand mark + "Studi0Trace" wordmark · engine/backend status pill (`v0.2.0 · potrace, vtracer`, or "Waking server…" during cold start) · theme toggle (system/light/dark, persisted) · **New image** button (ghost; in workspace state only).

### Canvas card

- **View modes** (muted-capsule tabs): **Split** (draggable divider, default), **Side by side**, **Overlay** (vector over source with opacity slider), **Vector**.
- Zoom: fit / 100 % / − / + and wheel-zoom; drag to pan. Both layers share one transform so the original and the SVG are pixel-aligned — fixing the current mismatch where the raster shows at natural size and the SVG fills the box.
- Checkerboard behind transparent regions (both layers).
- Updating state: the previous result stays visible with a subtle 60 % opacity + progress bar in the card header. No flash to a spinner.
- **Stats strip** under the canvas: engine, paths, nodes, size (human bytes), time (ms), plus per-engine error inline if any.
- **Actions**: Download SVG · Download PNG (client-side raster via `<canvas>` at 1×/2×/4×) · Copy SVG.

### Controls card

- **Engine tabs** from `GET /engines` (label + one-line description). Adding an engine on the backend adds a tab.
- **Parameter groups** from each param's `ui.group` (e.g. Bitmap / Cleanup / Curves / Colour / Output). Controls by `ui.control`:
  - `slider` → Radix Slider + numeric input, `ui.step`, `ui.unit`, min/max from schema, label from `ui.label` or title-cased name, `description` as helper text.
  - `select` → Radix Select over the enum.
  - `toggle` → Radix Switch.
- **Reset to defaults** (per engine). Param state persists per engine in `localStorage` (`studi0trace.params.<engine>`).
- **Compare all** toggle: runs every engine on each change and shows a compact per-engine stats table below the tabs, so the user can pick the best engine for this image.

## Data flow

```
File → hash (SHA-256 of bytes) → POST /uploads → image_id (cached in memory & sessionStorage)
Param change → debounce 250 ms → cancel in-flight (AbortController)
             → POST /vectorize {image_id, engines, parameters}
             → TanStack Query cache keyed [hash, engine, paramsJSON] → instant when revisiting a setting
```

### Backend additions (small, in this project)

- `POST /uploads` (multipart `file`) → `{image_id, width, height, format}`. Server keeps the validated `TraceInput` in an in-process LRU (`MAX_UPLOAD_CACHE_BYTES`, default 256 MB; TTL 30 min). This removes the 20 MB re-upload on every slider tick.
- `POST /vectorize` accepts `image_id` **or** `file`. Unknown/expired id → 404 `{"code": "image_expired"}`; the client re-uploads transparently and retries once.
- Remove legacy `vectorized` field and `selected_method` alias (the Vue app is gone).
- `GET /engines` unchanged.

Multi-worker note: the cache is per process. Render runs one uvicorn worker; if that changes, the client's re-upload fallback keeps things correct, just slower. Documented in README.

### Cold start & errors

- On mount, `GET /health` with a 4 s timeout; on failure show the "Waking server — free tiers sleep after inactivity, usually < 60 s" banner and retry every 5 s. Uploads/vectorize requests wait for health.
- Request errors → sonner toast with the server's `message`; 422 param errors highlight the offending control.
- Engine-level errors render inline in the stats strip for that engine; other engines still show.

## Visual details

- Poppins via `@fontsource/poppins` (400/500/600). Mono for stats numbers uses `font-variant-numeric: tabular-nums`, not a mono face.
- Buttons h-10, `rounded-md`; icon buttons 40 × 40; visible `ring` focus.
- Cards: `rounded-lg border bg-card shadow-sm`, `p-4` compact.
- Brand mark: Studi0 squircle outline (from `studi0mail/ui/public/brand/logomark.svg`) with a yellow bezier "trace" stroke inside. Delivered as `public/brand/studi0trace-mark.svg` + PNG icons 192/512 + favicon; `manifest.webmanifest`; **no service worker** (the old `sw.js` is dropped — a caching SW on a tool whose API contract just changed is a liability).
- Dark mode is the default when the OS prefers it; explicit choice persists.
- Layout works at 375 px: header condenses to mark + status; canvas full-width; controls stack below with a sticky "Controls" toggle.

## Out of scope

Accounts, history, server persistence, SVG editing, the Vexel engine itself, hosting changes (Render free tier stays; see Open question).

## Open question for Tim

Cold start is a **hosting** problem: Render's free tier sleeps. The UI handles it gracefully, but "fast" ultimately means a paid instance ($7/mo starter) or a different host (Fly.io machines with auto-stop wake in ~1 s; a small VPS). Not decided here.

## Testing

- **Vitest + Testing Library + MSW** (`frontend/src/**/*.test.tsx`): 
  - `ParamControl` renders slider/select/toggle from schema fragments and emits typed values.
  - `EngineTabs` renders one tab per engine from a mocked `/engines`.
  - `Dropzone` accepts drop, file input and paste; rejects a `.txt` with a toast.
  - `useVectorize`: debounces, cancels the earlier request, retries once on `image_expired`.
  - `Comparison`: switching view modes, split divider drag updates clip.
  - `formatBytes`, `paramsToQueryKey` unit tests.
- **Backend**: `/uploads` + `image_id` path, expiry 404, LRU eviction, legacy fields gone (`tests/test_api.py` updated).
- **Manual gate** in the in-app browser: upload via drop + paste, switch engines, drag sliders (watch for cancellations in the network log), all four view modes, zoom/pan, downloads, dark/light, 375 px width.

## Rollout

1. Backend: `/uploads`, `image_id`, remove legacy fields, tests.
2. New `frontend/` scaffold (Vite React TS, Tailwind tokens, fonts, brand), delete Vue app.
3. API client + health/wake hook + upload hook.
4. Dropzone + empty state + samples.
5. Controls: engine tabs + schema-driven params + persistence + reset.
6. Canvas: view modes, zoom/pan, stats strip, actions.
7. Compare-all, theme toggle, responsive pass, tests, browser gate, README/CLAUDE.md updates, `render.yaml` check.
