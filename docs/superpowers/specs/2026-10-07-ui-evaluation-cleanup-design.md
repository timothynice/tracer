# Studi0Trace UI evaluation and cleanup — design

**Date:** 2026-10-07
**Scope:** the Mac app's frontend (`frontend/`) and the one Rust change PDF export needs (`apps/desktop/src-tauri`).
**Starts from:** main at 87b00c1, which has the AI redraw.

## Why

The app works, and it looks assembled rather than designed. What a user sees, in the order it bothers them:

1. Two grey families. The window is translucent over macOS's neutral "sidebar" vibrancy material, so the gutters and the 60 %-opaque image sidebar always show a neutral grey. In dark mode the `secondary`, `muted`, `accent`, `border` and `input` tokens sit at 12 % saturation while `card` and `window` sit at 19–22 %. In light mode `background` and `card` are pure neutral white next to a cool-tinted `window` and `secondary`. Three sources, one impression: cool greys mixed with flat ones.
2. Before a trace, split mode clips the source to the left half of the canvas, so a new image shows as half an image beside an empty half. Nothing says "not traced yet" except a pill.
3. Loading versus done is not clear. The trace button is three different controls across its states (an accent button; a muted "Cancel · 4 s" with a line above it; a disabled button with "Up to date" under it) and the layout jumps between them. A sidebar card shows a dot while tracing and nothing when traced.
4. Auto picks well, and the four other presets are drawn with the same weight as Auto: five icon tiles, five two-line cards.
5. Export offers SVG and PNG only.
6. Marketing copy sits in the window chrome: a tagline under the title and a header with a 40 px icon tile and "Convert your image to a clean, scalable vector."

## Decisions taken

- The vibrancy stays and is tinted cool; the window is not made opaque.
- PDF is the one new export format. EPS, DXF and copy-as-code are out.
- Auto is the hero; the styles collapse under a disclosure.
- A finished trace lands on split mode, as today; the pre-trace view changes.

## 1. One cool grey family

Every neutral token sits on hue 214–216. The accent stays the system accent (`--accent-mac`); the brand yellow (`--brand-accent`) stays, used only for the "Auto's pick" dot.

Dark mode (`.dark`):

| token | now | becomes |
| --- | --- | --- |
| `--background` | 210 15% 15% | 215 19% 14% |
| `--card`, `--popover` | 214 19% 18% | 215 19% 18% (unchanged in effect) |
| `--secondary`, `--muted` | 210 12% 22% | 215 18% 23% |
| `--accent` | 210 12% 24% | 215 18% 26% |
| `--border`, `--input` | 210 12% 20% | 215 18% 23% |
| `--muted-foreground` | 215 15% 65% | 215 16% 66% |
| `--window` | 216 22% 9% | unchanged |
| `--checker-a` / `-b` | 214 19% 20% / 210 15% 15% | 215 19% 21% / 215 19% 16% |

Light mode (`:root`):

| token | now | becomes |
| --- | --- | --- |
| `--background` | 0 0% 99% | 215 28% 98% |
| `--card`, `--popover` | 0 0% 100% | 215 30% 99.5% |
| `--secondary`, `--muted`, `--accent` | 210 40% 96% | 215 32% 95% |
| `--border`, `--input` | 214 32% 91% | 215 26% 90% |
| `--window` | 220 14% 93% | 215 22% 93% |
| `--checker-a` / `-b` | 0 0% 85% / 0 0% 77% | 215 12% 86% / 215 12% 79% |

The vibrancy tint: `html.native body` stops being transparent and paints `hsl(var(--window) / 0.55)` in dark and `hsl(var(--window) / 0.5)` in light, so the material shows through a cool wash. `.panel-sidebar` keeps its 60 % / 45 % card over that. The Settings window keeps its opaque background.

The `.mac-selected` ring, `.mac-choice[aria-checked]` tint and the switch are unchanged: they are the accent.

## 2. The canvas's states

The viewer gains a `stage` derived from its props: `untraced` (no SVG, no compare, not busy, no error), `tracing` (busy, no SVG), `retracing` (busy over an SVG), `traced`, `failed` (error, no SVG). The source image, the vector and the chrome are drawn by stage, and the view mode only matters once there is something to compare.

**Untraced.** The whole source image, fitted, as a ghost: `filter: saturate(0.3) blur(5px)`, opacity 0.55. No clip in split mode, no divider, no "Original" / "Vector" chips, in every mode. A pill in the centre: "Press ⌘↩ to trace" (the browser harness keeps the same words). The ghost is a CSS class on the image element (`.ghost`), so the same `<img>` sharpens in place.

**Tracing.** The ghost stays. A light band sweeps across the image's box every 1.4 s (a `linear-gradient` of `hsl(var(--foreground) / 0.08)`, 40 % wide, animated with the existing `slide` keyframes, clipped to the image's bounds, `mix-blend-mode: screen` in dark and `multiply` in light). The pill reads the phase and the elapsed seconds: "Tracing… 4 s", "Trying 4 styles… 12 s", "Queued…". The elapsed seconds are the job's `startedAt`, which the viewer is handed as `busy: { phase: string; startedAt: number }` (it was a string).

**Retracing** (a trace runs while a vector is on screen): the vector at 0.6 opacity and the 2 px accent progress bar at the top, as today. No sweep: the band would read as a glitch over a sharp vector.

**Traced.** The reveal, once per new SVG:
1. The ghost sharpens: `filter` and `opacity` transition over 300 ms.
2. In split mode the vector's clip starts at the right edge and moves to the divider's position over 450 ms (`transition: clip-path`), the divider and the chips fading in with it. In side-by-side, overlay and vector modes the vector fades in over 300 ms.

A new SVG from a re-trace over an existing vector swaps without the wipe (the split is already open). `prefers-reduced-motion` and the test environment skip both: the final state is painted at once. The reveal is driven by a `revealKey` (the SVG's identity) in a `useEffect`, not by mount, so a mode change or a resize never replays it.

**Failed.** The error card over the ghost, as today.

The drift check (`compare`) is a traced stage with the redraw in the vector's place, as today.

## 3. Done versus loading

**The trace button is one control** (`TraceButton`): the same `h-9 w-full rounded-lg` button in every state, so nothing moves.

| state | look | label | click |
| --- | --- | --- | --- |
| idle, no vector | accent | ✦ Generate Vector | generate |
| stale (`needsUpdate`) | accent | ✦ Update Vector | generate |
| queued | accent at 0.8, no sweep | Queued… · ■ | cancel |
| tracing | accent with an indeterminate sweep (`slide` keyframes over a lighter band) | Tracing… 4 s · ■ | cancel |
| auto tracing | the same | Trying 4 styles… 12 s · ■ | cancel |
| up to date | muted (`.mac-button`), disabled | ✓ Up to date | — |

The "■" is a 10 px square (`Square` from lucide, filled), the stop glyph. Its title stays "Cancel Trace (⌘.)". The stats line ("41 shapes · 369 nodes · 12 KB · 6.8 s") stays above the button. The "Up to date" line under the button goes: the button says it.

**Sidebar cards** (`ImageCard`): a traced image whose vector is current carries a small badge at the thumbnail's bottom-right: "SVG" in 10 px semibold on `bg-popover/85` with `backdrop-blur`, rounded. A stale trace (`needsUpdate`) turns the badge's text amber (`text-warning`) and its title says "Settings changed since this trace". While a job runs the thumbnail dims to 0.6 and the dot pulses, as today; a failure keeps the red dot. The "· Tracing…" suffix on the size line stays.

## 4. Auto as the hero

`PresetCards` renders one radiogroup with two parts.

**The Auto card**, first in the group: `p-3`, the sparkle in a 36 px tile as today, the label "Auto" at 13 px semibold, and one line at 12 px:

- before any run: "Tries 4 styles and keeps the cleanest faithful one."
- running: "Trying 4 styles…"
- after: "Chose Simplified — the cleanest at the same fidelity" (the existing `autoChoice`).
- Auto could not choose: "Couldn't choose — <reason>".

**"Choose a style"**, a disclosure button under the Auto card (the same `mac-ghost` row as "Advanced Options", with the chevron). It is closed by default and opens itself when the active preset is not Auto (so hand-set values and a picked style are never hidden). While open it lists every non-Auto preset as a compact row (`h-9`, `px-2.5`):

- the radio dot (`.mac-radio`) at the left,
- the label at 13 px, with the brand yellow dot (`.dot-brand`) before it when it is Auto's pick (the "Auto's pick" chip goes),
- the verdict at the right in 11 px muted: a dot (`bg-success` / `bg-warning`) and "Clean" or the issues, truncated; a candidate's error in `text-destructive`; nothing when there is no run.

The candidates come first, then a thin divider, then the trailing presets (`cutfile`, `flat`, …) — the "More Styles" toggle goes, since the rows are a third the height of the cards. The icon tiles go with it; `PRESET_ICONS` keeps only `auto`. Arrow keys move through Auto and the rows in order, as today; `aria-expanded` and the `radiogroup` roles stay.

## 5. PDF export

- `ExportFile.kind` becomes `"svg" | "png" | "pdf"`. For `pdf` the frontend sends the SVG text as the bytes, with the name `<stem>.pdf`.
- The desktop's `export_file` reads `x-kind: pdf`, converts the bytes with `svg2pdf` (pure Rust, usvg-based; fonts are not needed since the engine writes no text) and writes the PDF. The panel's title is "Export PDF", the filter "PDF document". Conversion failures return `CommandError::bad_request` with the message "This vector could not be converted to PDF."
- `ExportMenu` adds "Export PDF…" between "Export SVG…" and the PNG sizes, disabled where the platform cannot (`platform.canExportPdf`, true in the Mac app, false in the browser harness).
- The Mac menu adds File ▸ Export ▸ "Export PDF…" (`export-pdf`, no shortcut), enabled with `has_vector`; `MenuCommand` and `READS_SETTINGS` gain `export-pdf`.
- Export All stays SVG.
- A PDF export marks the trace exported, like SVG and PNG.

## 6. Chrome diet

- `TitleBar`: the mark and "Studi0Trace", no tagline. The bar's height stays 52 px.
- `VectorizePanel`'s header: "Vectorize" at 15 px semibold on one line with the AI-redraw chip ("AI redraw", with Show Original and Revert) at its right when the image is a redraw; no icon tile, no sentence. The redraw hint and button keep their place below.
- `EmptyState` keeps "Drop images here" and gains the tagline "Turn images into clean vectors" as its heading above the drop zone, in the brand face.

## Out of scope

Settings, the layer inspector, the viewer toolbar's layout, the context menus, the web harness's look beyond what the shared components change.

## Tests

Frontend (`vitest`, jsdom):
- `Viewer`: untraced shows the source whole (no `clip-path` on the source pane) with the ghost class and no divider or chips, in split and side-by-side; tracing shows the sweep and the phase with seconds; traced shows the split with chips; a re-trace keeps the vector at 0.6 with the progress bar.
- `TraceButton`: the four looks and labels, the stop click cancels, the elapsed seconds tick.
- `ImageCard`: the SVG badge on a current trace, amber on a stale one, none without a trace.
- `PresetCards`: Auto first with the three lines; the disclosure closed by default, open when a style is active, the rows' verdicts and the yellow dot on the pick; arrows traverse Auto and the rows.
- `ExportMenu`: the PDF item, disabled when the platform cannot export PDF; `useExports` names the file `<stem>.pdf` and sends the SVG text.
- `App.commands`: `export-pdf` reaches the export.
- `TitleBar` / `VectorizePanel`: no tagline, the plain header.

Rust (`cargo test -p studi0trace-desktop --release`): `export::to_pdf` on a fixture SVG with a linear gradient and a `feGaussianBlur` filter yields bytes starting `%PDF-`; a malformed SVG is a `bad_request`. `menu.rs`'s table test gains `export-pdf`.

Verification: `npm run test:run`, `npm run build`, `cargo test -p studi0trace-desktop --release`; the app in both appearances with screenshots of the untraced, tracing, traced and failed canvas, the preset section open and closed, and the trace button in each state.
