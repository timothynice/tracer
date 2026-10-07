# UI Evaluation and Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One cool grey family, a canvas that says untraced / tracing / traced, one trace button, Auto as the hero with the styles folded under it, PDF export, and the marketing copy out of the chrome.

**Architecture:** Frontend-only except PDF: the tokens move in `styles.css`; `Viewer` derives a `stage` from its props and draws the ghost, the sweep and the reveal by it; `TraceButton`, `ImageCard`, `PresetCards`, `TitleBar`, `VectorizePanel`, `EmptyState` are restyled in place. PDF adds a kind to the platform contract and a conversion in the desktop crate's `export.rs` through `svg2pdf`.

**Tech Stack:** React 18 + TypeScript + Tailwind 3 (`frontend/`, vitest + jsdom + Testing Library); Tauri 2 desktop crate in Rust (`apps/desktop/src-tauri`, `svg2pdf`).

**Spec:** `docs/superpowers/specs/2026-10-07-ui-evaluation-cleanup-design.md`

## Global Constraints

- Every neutral token sits on hue 214–216; the accent is `--accent-mac`; the brand yellow is used only for the "Auto's pick" dot.
- Never a one-sided coloured border as a highlight (CLAUDE.md).
- Copy, verbatim: "Press ⌘↩ to trace", "Tracing…", "Trying N styles…", "Queued…", "Up to date", "Generate Vector", "Update Vector", "Choose a style", "Tries N styles and keeps the cleanest faithful one.", "Export PDF…", "This vector could not be converted to PDF.", "Settings changed since this trace", "Turn images into clean vectors".
- `prefers-reduced-motion` and the test environment (no `matchMedia`) skip the reveal and the sweep's motion.
- Frontend commands: `cd frontend && npm run test:run`, `npm run build`. Rust: `cargo test -p studi0trace-desktop --release` at the repo root (after `cd frontend && npm run build`, since the crate embeds `frontend/dist`).
- Commit after each task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 1: One cool grey family and the vibrancy tint

**Files:**
- Modify: `frontend/src/styles.css` (the `:root` and `.dark` blocks, the `html.native` rules; add `.ghost`, `.sweep`)
- Create: `frontend/src/styles.test.ts`

**Interfaces:**
- Produces: CSS classes `.ghost` (desaturated, blurred, half-opaque source; transitions back when removed) and `.sweep` (a light band moving across the element; `--sweep` colours it) used by Tasks 2 and 3.

- [ ] **Step 1: Write the failing test** — every HSL token in `:root` and `.dark` is cool, except the named exceptions.

```ts
// frontend/src/styles.test.ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const block = (selector: string) => css.slice(css.indexOf(`${selector} {`)).split("}")[0];
const tokens = (text: string) => [...text.matchAll(/--([a-z-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%/g)].map((m) => ({ name: m[1], h: Number(m[2]), s: Number(m[3]) }));
// the accent, the status colours and the brand yellow are not greys
const COLOURED = new Set(["primary-foreground", "brand-accent", "success", "warning", "info", "destructive"]);

describe("the design tokens", () => {
  it.each([":root", ".dark"])("%s: every neutral sits on one cool hue", (selector) => {
    const greys = tokens(block(selector)).filter((t) => !COLOURED.has(t.name));
    expect(greys.length).toBeGreaterThan(10);
    for (const t of greys) {
      expect(t.h, t.name).toBeGreaterThanOrEqual(214);
      expect(t.h, t.name).toBeLessThanOrEqual(216);
      expect(t.s, t.name).toBeGreaterThan(10);
    }
  });

  it("tints the vibrancy instead of leaving the window transparent", () => {
    expect(css).toMatch(/html\.native body \{[^}]*hsl\(var\(--window\) \/ 0\.5\)/);
    expect(css).toMatch(/html\.native\.dark body \{[^}]*hsl\(var\(--window\) \/ 0\.55\)/);
  });
});
```

- [ ] **Step 2: Run it to see it fail** — `cd frontend && npx vitest run src/styles.test.ts`. Expected: the light `background` (hue 0) fails the hue check; the tint assertions fail.

- [ ] **Step 3: Move the tokens.** Replace the `:root` and `.dark` token values with these (every other line in the blocks stays):

```css
:root {
  color-scheme: light;
  --background: 215 28% 98%;
  --foreground: 222 47% 11%;
  --card: 215 30% 99.5%;
  --card-foreground: 222 47% 11%;
  --popover: 215 30% 99.5%;
  --popover-foreground: 222 47% 11%;
  --primary: 215 20% 30%;
  --primary-foreground: 58 100% 74%;
  --secondary: 215 32% 95%;
  --secondary-foreground: 222 47% 11%;
  --muted: 215 32% 95%;
  --muted-foreground: 215 16% 47%;
  --accent: 215 32% 95%;
  --accent-foreground: 222 47% 11%;
  --destructive: 0 100% 68%;
  --destructive-foreground: 0 0% 100%;
  --border: 215 26% 90%;
  --input: 215 26% 90%;
  --ring: 215 20% 30%;
  --brand-accent: 58 100% 73%;
  --success: 142 71% 45%;
  --warning: 45 93% 47%;
  --info: 199 89% 48%;
  --radius: 0.5rem;
  --checker-a: 215 12% 86%;
  --checker-b: 215 12% 79%;
  --window: 215 22% 93%;
  --accent-mac: #007aff;
}

.dark {
  color-scheme: dark;
  --background: 215 19% 14%;
  --foreground: 215 20% 98%;
  --card: 215 19% 18%;
  --card-foreground: 215 20% 98%;
  --popover: 215 19% 18%;
  --popover-foreground: 215 20% 98%;
  --primary: 215 20% 45%;
  --primary-foreground: 58 100% 74%;
  --secondary: 215 18% 23%;
  --secondary-foreground: 215 20% 98%;
  --muted: 215 18% 23%;
  --muted-foreground: 215 16% 66%;
  --accent: 215 18% 26%;
  --accent-foreground: 215 20% 98%;
  --destructive: 0 100% 68%;
  --destructive-foreground: 215 20% 98%;
  --border: 215 18% 23%;
  --input: 215 18% 23%;
  --ring: 215 20% 45%;
  --checker-a: 215 19% 21%;
  --checker-b: 215 19% 16%;
  --window: 216 22% 9%;
}
```

Note `--foreground` and `--destructive-foreground` in `:root` stay at hue 222 / 0: `foreground` is near-black text and the test's hue check applies to it too, so set `--foreground: 215 47% 11%` in `:root` as well (the hue is invisible at 11 % lightness). `--destructive-foreground: 0 0% 100%` is white: add `destructive-foreground` to the test's `COLOURED` set.

- [ ] **Step 4: Tint the vibrancy.** Replace the `html.native` rules in `@layer base`:

```css
  html.native {
    /* the window's vibrancy shows through where no panel is painted … */
    background: transparent;
  }
  /* … through a cool wash, so the material's neutral grey never reads against the panels */
  html.native body {
    background: hsl(var(--window) / 0.5);
  }
  html.native.dark body {
    background: hsl(var(--window) / 0.55);
  }
```

Keep the Settings-window rule after it (it paints `hsl(var(--background))` on both `html` and `body`, which must still win: it already comes later in the file).

- [ ] **Step 5: Add the stage classes** to `@layer components`:

```css
  /* An image that is not traced yet: unmistakably a preview. Removing the class sharpens it in place. */
  .ghost {
    filter: saturate(0.3) blur(5px);
    opacity: 0.55;
    transition:
      filter 300ms ease-out,
      opacity 300ms ease-out;
  }
  /* A light band crossing a box while something runs. `--sweep` is the band's colour. */
  .sweep {
    --sweep: hsl(var(--foreground) / 0.08);
    background: linear-gradient(100deg, transparent 30%, var(--sweep) 50%, transparent 70%);
    background-size: 250% 100%;
    animation: sweep 1.4s ease-in-out infinite;
    mix-blend-mode: multiply;
  }
  .dark .sweep {
    mix-blend-mode: screen;
  }
  .mac-primary .sweep {
    --sweep: rgb(255 255 255 / 0.22);
    mix-blend-mode: normal;
  }
```

and the keyframes beside `slide`:

```css
@keyframes sweep {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: 0 0;
  }
}
```

The reduced-motion block already zeroes every animation.

- [ ] **Step 6: Run the test and the build** — `npx vitest run src/styles.test.ts && npm run build`. Expected: both pass.

- [ ] **Step 7: Commit** — `git commit -am "frontend: one cool grey family; the vibrancy tinted; ghost and sweep classes"`.

---

### Task 2: The canvas's states

**Files:**
- Modify: `frontend/src/components/Viewer.tsx`
- Modify: `frontend/src/components/Viewer.test.tsx`
- Modify: `frontend/src/App.tsx:189-200` (the `busy` value handed to the viewer)

**Interfaces:**
- Consumes: `.ghost`, `.sweep` from Task 1.
- Produces: `ViewerProps.busy: { phase: string; startedAt: number } | null` (was `string | null`); `export type Stage`; `export function stageOf(...)`.

- [ ] **Step 1: Update the tests.** In `Viewer.test.tsx`:

Replace "the divider and its handle wait for a vector…" with:

```tsx
  it("before a trace the source shows whole and ghosted: no clip, no divider, no labels", () => {
    const { props, rerender } = setup({ svg: undefined });
    const pane = screen.getByTestId("source-pane");
    expect(pane).toHaveClass("ghost");
    expect(pane.style.clipPath).toBe("");
    expect(screen.queryByRole("separator", { name: /comparison divider/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Original")).toBeNull();
    expect(screen.queryByText("Vector")).toBeNull();
    expect(screen.getByText("Press ⌘↩ to trace")).toBeInTheDocument();
    rerender(<Viewer {...props} svg={SVG} />);
    expect(screen.getByTestId("source-pane")).not.toHaveClass("ghost");
    expect(screen.getByRole("separator", { name: /comparison divider/i })).toBeInTheDocument();
    expect(screen.getByText("Original")).toBeInTheDocument();
  });

  it("side by side before a trace is the same ghost, not two panes", () => {
    setup({ svg: undefined, mode: "side" });
    expect(screen.getByTestId("source-pane")).toHaveClass("ghost");
    expect(screen.queryByText("Original")).toBeNull();
  });
```

Replace "split leaves the vector side empty until a vector exists" with:

```tsx
  it("while tracing the ghost stays, a band sweeps it and the pill counts the seconds", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      setup({ svg: undefined, busy: { phase: "Tracing…", startedAt: Date.now() } });
      expect(screen.getByTestId("source-pane")).toHaveClass("ghost");
      expect(screen.getByTestId("sweep")).toBeInTheDocument();
      expect(screen.getByText("Tracing… 0 s")).toBeInTheDocument();
      act(() => vi.advanceTimersByTime(4000));
      expect(screen.getByText("Tracing… 4 s")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a queued job says so without counting", () => {
    setup({ svg: undefined, busy: { phase: "Queued…", startedAt: Date.now() } });
    expect(screen.getByText("Queued…")).toBeInTheDocument();
  });

  it("a re-trace over a vector dims it under the progress bar, with no sweep", () => {
    setup({ busy: { phase: "Tracing…", startedAt: Date.now() } });
    expect(screen.getByRole("progressbar", { name: "Tracing" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Vector result" })).toHaveClass("opacity-60");
    expect(screen.queryByTestId("sweep")).toBeNull();
  });
```

Remove "while busy it names the phase" (covered above). In "overlay has an opacity slider; errors and the busy bar show" change any `busy: "…"` to `busy: { phase: "Tracing…", startedAt: 0 }`. In "a failure is a persistent error…" add `expect(screen.getByTestId("source-pane")).toHaveClass("ghost");`.

- [ ] **Step 2: Run them** — `npx vitest run src/components/Viewer.test.tsx`. Expected: the new ones fail (type error on `busy`, no `ghost`, "Original" present).

- [ ] **Step 3: Implement.** In `Viewer.tsx`:

Change the prop and add the stage:

```ts
  /** What the wait is for ("Queued…", "Tracing…", "Trying 4 styles…") and when it began; null when nothing runs. */
  busy: { phase: string; startedAt: number } | null;
```

```ts
export type Stage = "untraced" | "tracing" | "retracing" | "traced" | "failed";

/** What the canvas is showing: a trace or a redraw, or until then the source as a ghost. */
export function stageOf(p: { svg?: string; compare?: unknown; busy: unknown; errorMessage?: string }): Stage {
  if (p.svg || p.compare) return p.busy ? "retracing" : "traced";
  if (p.errorMessage) return "failed";
  return p.busy ? "tracing" : "untraced";
}

const reducedMotion = () => typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
```

Inside the component, after `const shown = …`:

```ts
  const stage = stageOf({ svg, compare, busy, errorMessage });
  // the pill's seconds
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!busy) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);
  const counting = busy && busy.phase !== "Queued…";
  const pill = busy ? (counting ? `${busy.phase} ${Math.max(0, Math.floor((now - busy.startedAt) / 1000))} s` : busy.phase) : "Press ⌘↩ to trace";

  // The reveal: once, when a vector (or a redraw) first arrives. "start" paints the vector clipped away, "run" lets it wipe in.
  const [wipe, setWipe] = useState<"start" | "run" | null>(null);
  const had = useRef(shown);
  useEffect(() => {
    const arrived = shown && !had.current;
    had.current = shown;
    if (!arrived || reducedMotion()) return;
    setWipe("start");
    const frame = requestAnimationFrame(() => setWipe("run"));
    const done = setTimeout(() => setWipe(null), 900);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(done);
    };
  }, [shown]);
```

Replace the `source`, `vector` and the whole `mode === "side" ? … : …` block so that:

- the source always sits in one `<div data-testid="source-pane">` whose class is `absolute inset-0` plus `ghost` when `stage` is `untraced`, `tracing` or `failed`, and whose `clipPath` is set only when `shown && mode === "split"`: `inset(0 ${wipe === "start" ? 0 : (1 - split) * 100}% 0 0)`; it carries `transition-[clip-path] duration-[450ms] delay-150 ease-out` while `wipe === "run"`;
- in `side` mode with `shown`, the two half panes render as today (the source pane gets `w-1/2` instead of `inset-0`); without `shown` the side-mode branch is not taken (it falls to the single-image branch, which draws the ghost);
- the vector's wrapper in split mode has `clipPath: inset(0 0 0 ${wipe === "start" ? 100 : split * 100}%)` with the same transition classes while `wipe === "run"`; in overlay / vector / side modes the vector gets `motion-fade` while `wipe !== null`;
- the divider and the chips render only when `shown`, with `motion-fade [animation-delay:300ms]` while `wipe !== null`;
- while `stage === "tracing"`: `<div data-testid="sweep" aria-hidden="true" className="sweep pointer-events-none absolute left-0 top-0" style={imgStyle} />` after the source pane;
- the centre overlay for `!shown` keeps the error card, and otherwise shows one pill: `<p className="rounded-full border bg-popover/90 px-3.5 py-1.5 text-[13px] font-medium shadow-sm backdrop-blur" aria-live="polite">` containing a `Loader2` spinner when `busy` and the `pill` text (muted colour when not busy).

- [ ] **Step 4: Hand the viewer the job.** In `App.tsx` replace the `busy` line:

```ts
  const candidates = catalog.presets.filter((p) => p.auto_candidate).length;
  const busy = job ? { phase: job.phase === "queued" ? "Queued…" : job.key === "auto" ? `Trying ${candidates} styles…` : "Tracing…", startedAt: job.startedAt } : null;
```

- [ ] **Step 5: Run the viewer tests, then everything** — `npx vitest run src/components/Viewer.test.tsx && npm run test:run`. Fix any test elsewhere that passed `busy` as a string (grep `busy:` and `busy=` in `src/**/*.test.tsx`).

- [ ] **Step 6: Commit** — `git commit -am "frontend: the canvas says untraced, tracing, traced: the whole source as a ghost, a sweep while it runs, a wipe when the vector arrives"`.

---

### Task 3: One trace button

**Files:**
- Modify: `frontend/src/components/TraceButton.tsx`
- Modify: `frontend/src/components/TraceButton.test.tsx`

**Interfaces:**
- Consumes: `.sweep` (Task 1), `currentJob`, `needsUpdate`, `traceKey` from `@/state/library`.
- Produces: the same `TraceButtonProps`.

- [ ] **Step 1: Update the tests.** Replace the three state tests:

```tsx
  it("is Up to date when the trace on screen is the settings', and Update when they moved", () => {
    const key = paramsKey({ detail: 6 });
    const { rerender } = render(<TraceButton item={{ ...base, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Generate Vector/ })).toBeNull();
    rerender(<TraceButton item={{ ...base, params: { detail: 9 }, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Update Vector/ })).toBeEnabled();
  });

  it("while a job runs the one button names the phase, counts the seconds and cancels", () => {
    const onCancel = vi.fn();
    render(<TraceButton item={{ ...base, preset: "auto", job: { id: "j", key: "auto", startedAt: Date.now(), phase: "tracing" } }} candidates={4} onGenerate={vi.fn()} onCancel={onCancel} />);
    expect(screen.getByRole("button", { name: "Trying 4 styles… 0 s" })).toHaveAttribute("title", "Cancel Trace (⌘.)");
    expect(screen.getByTestId("sweep")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    fireEvent.click(screen.getByRole("button", { name: "Trying 4 styles… 3 s" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("a job for other settings is not this button's: it shows the settings' own state", () => {
    const key = paramsKey({ detail: 6 });
    render(<TraceButton item={{ ...base, traces: { [key]: answer }, shown: key, job: { id: "j", key: "auto", startedAt: Date.now(), phase: "tracing" } }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByTestId("sweep")).toBeNull();
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
  });

  it("a queued job says so, without the sweep, and still cancels", () => {
    const onCancel = vi.fn();
    render(<TraceButton item={{ ...base, job: { id: "j", key: paramsKey({ detail: 6 }), startedAt: Date.now(), phase: "queued" } }} candidates={4} onGenerate={vi.fn()} onCancel={onCancel} />);
    expect(screen.queryByTestId("sweep")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Queued…" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
```

- [ ] **Step 2: Run** — `npx vitest run src/components/TraceButton.test.tsx`. Expected: fail.

- [ ] **Step 3: Implement.**

```tsx
import { Check, Square, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { currentJob, needsUpdate, traceKey, type ImageItem } from "@/state/library";

export interface TraceButtonProps {
  item: ImageItem;
  /** How many styles Auto tries, for "Trying 4 styles…". */
  candidates: number;
  onGenerate: () => void;
  onCancel: () => void;
}

/** One button in every state, the same size, so nothing moves: generate, cancel while it runs, Up to date after. */
export function TraceButton({ item, candidates, onGenerate, onCancel }: TraceButtonProps) {
  const job = currentJob(item);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!job) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [job]);

  if (job) {
    const seconds = Math.max(0, Math.floor((now - job.startedAt) / 1000));
    const queued = job.phase === "queued";
    const phase = queued ? "Queued…" : `${job.key === "auto" ? `Trying ${candidates} styles…` : "Tracing…"} ${seconds} s`;
    return (
      <button type="button" className={`mac-primary relative overflow-hidden ${queued ? "opacity-80" : ""}`} onClick={onCancel} title="Cancel Trace (⌘.)">
        {!queued && <span data-testid="sweep" aria-hidden="true" className="sweep pointer-events-none absolute inset-0" />}
        <span className="relative tabular" aria-live="polite">
          {phase}
        </span>
        <Square className="relative h-2.5 w-2.5 fill-current" aria-hidden="true" />
      </button>
    );
  }
  if (item.shown !== null && item.shown === traceKey(item)) {
    return (
      <button type="button" className="mac-button h-9 w-full" disabled>
        <Check className="h-4 w-4" aria-hidden="true" />
        Up to date
      </button>
    );
  }
  const label = needsUpdate(item) ? "Update Vector" : "Generate Vector";
  return (
    <button type="button" className="mac-primary" onClick={onGenerate} title={`${label} (⌘↩)`}>
      <Wand2 className="h-4 w-4" aria-hidden="true" />
      {label}
    </button>
  );
}
```

- [ ] **Step 4: Run** — `npx vitest run src/components/TraceButton.test.tsx src/App.test.tsx src/App.commands.test.tsx`. Any App test that looked for "Cancel ·" or "Up to date" as text updates to the button names above.

- [ ] **Step 5: Commit** — `git commit -am "frontend: one trace button: generate, a sweeping cancel while it runs, Up to date after"`.

---

### Task 4: Done versus loading in the sidebar

**Files:**
- Modify: `frontend/src/components/ImageCard.tsx`
- Modify: `frontend/src/components/Sidebar.test.tsx`

**Interfaces:**
- Consumes: `shownAnswer`, `needsUpdate` from `@/state/library`.

- [ ] **Step 1: Add the test.** In `Sidebar.test.tsx`, after "lists every image…":

```tsx
  it("badges a traced image, amber when its settings have moved since, and dims one that is tracing", () => {
    const key = "balanced"; // a preset's key is its id when nothing is set by hand
    const answer = { svg: "<svg/>", elapsedMs: 1, stats: {} };
    setup({
      items: [
        item("t", { preset: "balanced", traces: { [key]: answer }, shown: key }),
        item("s", { preset: "logo", traces: { [key]: answer }, shown: key }),
        item("u"),
        item("r", { job: { id: "j", key: "auto", startedAt: 0, phase: "tracing" } }),
      ],
    });
    const badges = screen.getAllByText("SVG");
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveAttribute("title", "Traced");
    expect(badges[1]).toHaveAttribute("title", "Settings changed since this trace");
    expect(badges[1]).toHaveClass("text-warning");
    expect(screen.getByRole("option", { name: /r\.png/ }).querySelector("img")).toHaveClass("opacity-60");
  });
```

Check how `traceKey` builds a key first (`frontend/src/state/library.ts`, `traceKey`): if a preset's key is not its bare id, build `key` with the same helper the file exports and use that in both items.

- [ ] **Step 2: Run** — `npx vitest run src/components/Sidebar.test.tsx`. Expected: fail (no "SVG").

- [ ] **Step 3: Implement** in `ImageCard.tsx`:

```tsx
import { errorOf, needsUpdate, shownAnswer, type ImageItem } from "@/state/library";

/** One image in the sidebar: its thumbnail, name and size; a dot while it is queued, tracing or failed; a badge once traced. */
export function ImageCard({ item, selected, onSelect }: ImageCardProps) {
  const { image, job } = item;
  const error = errorOf(item);
  const phase = job ? (job.phase === "queued" ? "Queued" : "Tracing…") : error ? "Failed" : null;
  const traced = !job && !!shownAnswer(item);
  const stale = traced && needsUpdate(item);
  return (
    <div id={`image-${image.id}`} role="option" aria-selected={selected} onClick={onSelect} className="block">
      <div className={`checker relative aspect-[4/3] overflow-hidden rounded-lg ${selected ? "mac-selected" : "ring-1 ring-border"}`}>
        <img src={image.previewUrl} alt="" draggable={false} className={`h-full w-full object-contain p-2 transition-opacity ${job ? "opacity-60" : ""}`} />
        {job && <span aria-hidden="true" className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${job.phase === "queued" ? "bg-warning" : "mac-dot animate-pulse"}`} />}
        {!job && error && <span title={error.message} className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-destructive" />}
        {traced && (
          <span title={stale ? "Settings changed since this trace" : "Traced"} className={`absolute bottom-1.5 right-1.5 rounded bg-popover/85 px-1 text-[10px] font-semibold leading-4 backdrop-blur ${stale ? "text-warning" : "text-muted-foreground"}`}>
            SVG
          </span>
        )}
      </div>
      …name and size as today…
    </div>
  );
}
```

- [ ] **Step 4: Run** — `npx vitest run src/components/Sidebar.test.tsx`. Expected: pass.

- [ ] **Step 5: Commit** — `git commit -am "frontend: a traced image wears an SVG badge, amber once its settings move; a tracing thumbnail dims"`.

---

### Task 5: Auto as the hero

**Files:**
- Modify: `frontend/src/components/PresetCards.tsx` (rewrite the component; keep `activePreset`, `autoChoice`, `firstSentence`, `PRESET_ICONS`)
- Modify: `frontend/src/components/PresetCards.test.tsx`

**Interfaces:**
- Produces: the same `PresetCardsProps`; `PRESET_ICONS` keeps only `auto`.

- [ ] **Step 1: Update the tests.** Replace "is a radio group, Auto first, the styles Auto never picks folded away", "says the first sentence…", "after Auto…", "while Auto runs…" with:

```tsx
  it("is a radio group with Auto first and the styles folded under Choose a style", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={vi.fn()} />);
    const group = screen.getByRole("radiogroup", { name: "Preset" });
    expect(within(group).getAllByRole("radio")).toHaveLength(1);
    expect(within(group).getByRole("radio", { name: "Auto" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Tries 2 styles and keeps the cleanest faithful one.")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Choose a style" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-label"))).toEqual(["Auto", "Balanced", "Logo & icon", "Flat & poster"]);
    expect(screen.getByRole("radio", { name: "Balanced" })).toHaveAttribute("title", expect.stringContaining("Gradients, shadows, strokes and overlaps all reconstructed."));
  });

  it("opens itself when a style is the active one, and hands back the preset picked", () => {
    const onPick = vi.fn();
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="logo" auto={null} autoRunning={false} onPick={onPick} />);
    expect(screen.getByRole("button", { name: "Choose a style" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("radio", { name: "Logo & icon" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Balanced" }));
    expect(onPick).toHaveBeenCalledWith(VEXEL_PRESETS[1]);
  });

  it("after Auto: what it chose and why, each style's verdict, and the pick marked with the brand dot", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={auto} autoRunning={false} onPick={vi.fn()} />);
    expect(screen.getByLabelText("Auto chose Logo & icon — the cleanest at the same fidelity")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Choose a style" }));
    expect(within(screen.getByRole("radio", { name: "Balanced" })).getByText("2 pinholes")).toBeInTheDocument();
    const logo = screen.getByRole("radio", { name: "Logo & icon" });
    expect(within(logo).getByText("Auto's pick")).toHaveClass("sr-only");
    expect(logo.querySelector(".dot-brand")).toBeInTheDocument();
    expect(within(logo).getByText("Clean")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Balanced" }).querySelector(".dot-brand")).toBeNull();
  });

  it("while Auto runs it says so", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning onPick={vi.fn()} />);
    expect(screen.getByText("Trying 2 styles…")).toBeInTheDocument();
  });
```

In the arrow-key test, the names become exact: `{ name: "Auto" }`, `{ name: "Balanced" }`, `{ name: "Logo & icon" }`; the `Controlled` wrapper is unchanged (the disclosure opens itself once a style is picked, so the second arrow finds the next row rendered). Keep "the helpers".

- [ ] **Step 2: Run** — `npx vitest run src/components/PresetCards.test.tsx`. Expected: fail.

- [ ] **Step 3: Rewrite the component.**

```tsx
import { ChevronRight, Sparkles, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { AutoResult, ParamValues, Preset } from "@/lib/api";

/** Auto's icon; the styles are rows without one. */
export const PRESET_ICONS: Record<string, LucideIcon> = { auto: Sparkles };

export function firstSentence(text: string): string { …as today… }
export function activePreset(…): string | null { …as today… }
export function autoChoice(…): string { …as today… }

export interface PresetCardsProps { …as today… }

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Auto as the hero, and every style one row under "Choose a style": one radiogroup, the arrows walking all of it. */
export function PresetCards({ presets, defaults, values, active, auto, autoRunning, onPick, disabled }: PresetCardsProps) {
  const on = active ?? activePreset(presets, values, defaults);
  const autoPreset = presets.find((p) => p.kind === "auto") ?? null;
  const styles = presets.filter((p) => p.kind !== "auto");
  const candidates = styles.filter((p) => p.auto_candidate);
  const trailing = styles.filter((p) => !p.auto_candidate);
  const ordered = autoPreset ? [autoPreset, ...candidates, ...trailing] : [...candidates, ...trailing];
  const styleOn = on !== null && on !== "auto";
  const [open, setOpen] = useState(styleOn || !autoPreset);
  useEffect(() => {
    if (styleOn) setOpen(true); // a chosen style is never hidden
  }, [styleOn]);
  const rows = useRef(new Map<string, HTMLElement>());
  // a pick by the arrow keys focuses its row once it is rendered (the disclosure may be opening for it)
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    if (pendingFocus.current) {
      rows.current.get(pendingFocus.current)?.focus();
      pendingFocus.current = null;
    }
  });
  const byId = new Map((auto?.candidates ?? []).map((c) => [c.preset, c]));

  const move = (e: KeyboardEvent, at: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (step) {
      e.preventDefault();
      const next = ordered[Math.max(0, Math.min(ordered.length - 1, at + step))];
      if (next) {
        pendingFocus.current = next.id;
        onPick(next);
      }
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      onPick(ordered[at]);
    }
  };
  const bind = (p: Preset, at: number) => ({
    ref: (el: HTMLElement | null) => (el ? rows.current.set(p.id, el) : rows.current.delete(p.id)),
    role: "radio" as const,
    "aria-checked": p.id === on,
    "aria-disabled": disabled || undefined,
    "aria-label": p.label,
    tabIndex: p.id === on || (on === null && at === 0) ? 0 : -1,
    title: `${p.description}\n${p.detail}`,
    onClick: () => !disabled && onPick(p),
    onKeyDown: (e: KeyboardEvent) => !disabled && move(e, at),
  });

  const n = candidates.length;
  let autoLine = `Tries ${n} styles and keeps the cleanest faithful one.`;
  let autoLabel: string | undefined;
  if (autoRunning) autoLine = `Trying ${n} styles…`;
  else if (auto) {
    const choice = autoChoice(auto, presets);
    autoLine = capital(choice);
    autoLabel = `Auto ${choice}`;
  }

  const row = (p: Preset, at: number) => {
    const cand = byId.get(p.id);
    let verdict = null;
    if (cand?.scores)
      verdict = (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cand.scores.clean ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
          <span className="truncate">{cand.scores.clean ? "Clean" : cand.scores.issues.join(", ")}</span>
        </span>
      );
    else if (cand?.error) verdict = <span className="truncate text-[11px] text-destructive">{cand.error.message}</span>;
    return (
      <div key={p.id} {...bind(p, at)} className="mac-choice flex h-9 items-center gap-2.5 px-2.5 py-0">
        <span className="mac-radio" aria-hidden="true" />
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {auto?.pick === p.id && (
            <>
              <span className="dot-brand shrink-0" aria-hidden="true" />
              <span className="sr-only">Auto's pick</span>
            </>
          )}
          <span className="truncate text-[13px]">{p.label}</span>
        </span>
        {verdict}
      </div>
    );
  };

  return (
    <section className="space-y-1.5">
      <div role="radiogroup" aria-label="Preset" className="space-y-1.5">
        {autoPreset && (
          <div {...bind(autoPreset, 0)} className="mac-choice flex items-center gap-3 p-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold">{autoPreset.label}</span>
              <span className="mt-0.5 block text-[12px] leading-snug text-muted-foreground" aria-live="polite" aria-label={autoLabel}>
                {autoLine}
              </span>
            </span>
            <span className="mac-radio" aria-hidden="true" />
          </div>
        )}
        {autoPreset && (
          <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="mac-ghost h-8 w-full px-1.5 text-foreground">
            <ChevronRight className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
            Choose a style
          </button>
        )}
        {open && (
          <div className="space-y-0.5">
            {candidates.map((p, i) => row(p, (autoPreset ? 1 : 0) + i))}
            {trailing.length > 0 && candidates.length > 0 && <div aria-hidden="true" className="mx-2.5 my-1 h-px bg-border" />}
            {trailing.map((p, i) => row(p, (autoPreset ? 1 : 0) + candidates.length + i))}
          </div>
        )}
      </div>
    </section>
  );
}
```

`getByLabelText("Auto chose …")` finds the line by its `aria-label`; the test's `getByRole("radio", { name: "Auto" })` works because `aria-label` names the card.

- [ ] **Step 4: Run** — `npx vitest run src/components/PresetCards.test.tsx src/components/VectorizePanel.test.tsx src/App.test.tsx`. Fix any App test that clicked a preset by a two-line name or "More Styles".

- [ ] **Step 5: Commit** — `git commit -am "frontend: Auto is the hero; the styles are rows under Choose a style, their verdicts at the right, the pick a brand dot"`.

---

### Task 6: PDF export

**Files:**
- Modify: `frontend/src/platform/types.ts` (`ExportKind`, `ExportFile.kind`, `Platform.canExportPdf`, `MenuCommand`)
- Modify: `frontend/src/platform/native.ts`, `frontend/src/platform/web.ts` (`canExportPdf`; web's `exportFile` refuses pdf)
- Modify: `frontend/src/hooks/useExports.ts`, `frontend/src/hooks/useExports.test.tsx`
- Modify: `frontend/src/components/ExportMenu.tsx`, `frontend/src/components/ExportMenu.test.tsx`
- Modify: `frontend/src/App.tsx` (`READS_SETTINGS`, `run`'s switch, `ExportMenu`'s `canPdf`)
- Modify: `apps/desktop/src-tauri/Cargo.toml`, `apps/desktop/src-tauri/src/export.rs`, `apps/desktop/src-tauri/src/commands.rs:119-160`, `apps/desktop/src-tauri/src/menu.rs`
- Modify: any frontend test that builds a fake `platform` (`useExports.test.tsx` `mocks.platform`, `App.*.test.tsx`) to carry `canExportPdf`.

**Interfaces:**
- Produces: `export type ExportKind = "svg" | "png" | "pdf"`; `Platform.canExportPdf: boolean`; `MenuCommand` gains `"export-pdf"`; `ExportMenuProps.canPdf: boolean`; Rust `export::to_pdf(svg: &[u8]) -> Result<Vec<u8>, CommandError>`.

- [ ] **Step 1: Frontend tests.** In `ExportMenu.test.tsx` add:

```tsx
  it("offers PDF where the platform can make one, and greys it out elsewhere", () => {
    const onExport = vi.fn();
    const { rerender } = render(<ExportMenu canExport anyVector canPdf onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Export PDF/ }));
    expect(onExport).toHaveBeenCalledWith("pdf", 1);
    rerender(<ExportMenu canExport anyVector canPdf={false} onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    open();
    expect(screen.getByRole("menuitem", { name: /Export PDF/ })).toHaveAttribute("aria-disabled", "true");
  });
```

and add `canPdf` to the existing renders. In `useExports.test.tsx` add:

```tsx
  it("exports a PDF as stem.pdf, sending the SVG text for the app to convert", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg edited/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("pdf", 1));
    const file = mocks.platform.exportFile.mock.calls[0][0];
    expect(file).toMatchObject({ kind: "pdf", imageId: "a", name: "logo.pdf" });
    expect(text(file.bytes)).toBe("<svg edited/>");
  });
```

- [ ] **Step 2: Run** — `npx vitest run src/components/ExportMenu.test.tsx src/hooks/useExports.test.tsx`. Expected: fail.

- [ ] **Step 3: Frontend implementation.**

`types.ts`:
```ts
export type ExportKind = "svg" | "png" | "pdf";
export interface ExportFile {
  kind: ExportKind;
  …
}
export type MenuCommand = | "export-svg" | "export-pdf" | "export-png-1" | …;
export interface Platform {
  readonly kind: "native" | "web";
  /** Whether `exportFile` can take a "pdf" (the Mac app converts it; a browser cannot). */
  readonly canExportPdf: boolean;
  …
```
`native.ts`: `canExportPdf: true,` after `kind: "native",`. `web.ts`: `canExportPdf: false,` and in `exportFile`: `if (file.kind === "pdf") throw new Error("PDF export needs the Mac app.");`.

`useExports.ts`:
```ts
    (kind: ExportKind, scale: number, target: ImageItem | null = item) =>
      …
          const bytes = kind === "png" ? new Uint8Array(await (await svgToPngBlob(text, target.image.width, target.image.height, scale)).arrayBuffer()) : new TextEncoder().encode(text);
          const name = kind === "svg" ? `${stem}.svg` : kind === "pdf" ? `${stem}.pdf` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
```

`ExportMenu.tsx`: add `canPdf: boolean` to the props and `onExport: (kind: ExportKind, scale: number) => void`; after the SVG item:
```tsx
            <DropdownMenu.Item className="mac-menu-item" disabled={!canExport || !canPdf} onSelect={() => onExport("pdf", 1)}>
              Export PDF…
            </DropdownMenu.Item>
```

`App.tsx`: `READS_SETTINGS` gains `"export-pdf"`; `case "export-pdf": return void exports.exportImage("pdf", 1);`; `<ExportMenu … canPdf={platform.canExportPdf} …/>`.

Add `canExportPdf: true` to the mocked platform in `useExports.test.tsx` and anywhere else a `Platform` literal is built (`grep -rn 'kind: "native"' src --include=*.test.*`).

- [ ] **Step 4: Run the frontend** — `npm run test:run && npm run build`. Expected: pass.

- [ ] **Step 5: Rust test.** In `export.rs`'s `mod tests`:

```rust
    #[test]
    fn a_vector_with_a_gradient_and_a_filter_becomes_a_pdf() {
        let svg = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/></linearGradient><filter id="b"><feGaussianBlur stdDeviation="2"/></filter></defs><rect width="64" height="64" fill="url(#g)"/><circle cx="32" cy="32" r="10" filter="url(#b)"/></svg>"##;
        let pdf = to_pdf(svg.as_bytes()).unwrap();
        assert!(pdf.starts_with(b"%PDF-"));
        assert!(pdf.len() > 500);
    }

    #[test]
    fn what_is_not_an_svg_is_refused_as_such() {
        assert_eq!(to_pdf(b"<svg").unwrap_err().code(), Some("bad_request"));
        assert_eq!(to_pdf(&[0xff, 0xfe]).unwrap_err().code(), Some("bad_request"));
    }
```

(Check `CommandError::code()`'s return type in `error.rs` and match the assertion to it.)

- [ ] **Step 6: Run** — `cargo test -p studi0trace-desktop --release export::` from the repo root. Expected: compile error, `to_pdf` missing.

- [ ] **Step 7: Rust implementation.** `Cargo.toml` dependencies: `cargo info svg2pdf` to read the feature list, then add `svg2pdf = { version = "0.13", default-features = false, features = ["filters"] }` (the filters feature rasterises `feGaussianBlur` through resvg; no `text`: the engine writes no text, and fonts would pull in fontdb). If the feature is named otherwise, use the one that enables filters. In `export.rs`:

```rust
/// The SVG the frontend holds, as a PDF: a vector PDF, gradients kept, filters rasterised.
pub fn to_pdf(svg: &[u8]) -> Result<Vec<u8>, CommandError> {
    let refuse = |_: ()| CommandError::bad_request("This vector could not be converted to PDF.");
    let text = std::str::from_utf8(svg).map_err(|_| refuse(()))?;
    let tree = svg2pdf::usvg::Tree::from_str(text, &svg2pdf::usvg::Options::default()).map_err(|_| refuse(()))?;
    svg2pdf::to_pdf(&tree, svg2pdf::ConversionOptions::default(), svg2pdf::PageOptions::default()).map_err(|_| refuse(()))
}
```

(`svg2pdf::to_pdf` returns `Result<Vec<u8>, ConversionError>` in 0.13; adjust if the installed version returns `Vec<u8>` outright.)

In `commands.rs` `export_file`:

```rust
    let kind = header(&request, "x-kind").unwrap_or_default();
    let ext = match kind.as_str() {
        "png" => "png",
        "pdf" => "pdf",
        _ => "svg",
    };
    let converted;
    let bytes: &[u8] = if ext == "pdf" {
        converted = export::to_pdf(bytes)?;
        &converted
    } else {
        bytes
    };
```

and the panel: title `"Export PDF"` for pdf, filter `("PDF document", &["pdf"])`. Everything after (`export::save`, the panel) takes `bytes` as before.

In `menu.rs`: `e("export-pdf", "Export PDF…", None)` after `export-svg` in `MENU`; `("export-pdf", s.has_vector, None)` after `export-svg` in `plan`; `.item(&item("export-pdf")?)` after `.item(&item("export-svg")?)` in the File menu; in the `plan` test beside the `export-svg` assertions add `assert!(get(&p, "export-pdf").1);` and `assert_eq!(get(&none, "export-pdf"), ("export-pdf", false, None));`.

- [ ] **Step 8: Run** — `cd frontend && npm run build && cd .. && cargo test -p studi0trace-desktop --release`. Expected: pass. Then `cd backend && .venv/bin/python -m pytest tests/test_vexel_lock_matches_workspace.py -q` (the lock test, since the workspace's dependencies moved); if it fails, run `.venv/bin/python -m tools.sync_vexel_lock` and re-run.

- [ ] **Step 9: Commit** — `git commit -am "export: PDF, a vector PDF through svg2pdf in the app; the menu item, the File menu, the platform says whether it can"`.

---

### Task 7: Chrome diet

**Files:**
- Modify: `frontend/src/components/TitleBar.tsx`, `frontend/src/components/TitleBar.test.tsx`
- Modify: `frontend/src/components/RedrawSection.tsx` (export `RedrawChip`; the section no longer renders the chip row), `frontend/src/components/RedrawSection.test.tsx`
- Modify: `frontend/src/components/VectorizePanel.tsx` (`redrawChip?: ReactNode`; the plain header), `frontend/src/components/VectorizePanel.test.tsx`
- Modify: `frontend/src/App.tsx` (pass `redrawChip`)
- Modify: `frontend/src/components/EmptyState.tsx`, `frontend/src/components/EmptyState.test.tsx`
- Check: `frontend/src/App.redraw.test.tsx` still finds "Show Original" and "Revert" (they move, not go).

**Interfaces:**
- Produces: `export function RedrawChip({ item, onShowOriginal, onRevert }: { item: ImageItem; onShowOriginal: () => void; onRevert: () => void })` — null unless the image is a redraw.

- [ ] **Step 1: Tests.** `TitleBar.test.tsx`: replace the tagline expectation with `expect(screen.queryByText("Turn images into clean vectors")).toBeNull();`. `VectorizePanel.test.tsx`: in the first test add `expect(screen.queryByText("Convert your image to a clean, scalable vector.")).toBeNull();` and a render with `redrawChip={<span>chip</span>}` asserting `screen.getByText("chip")` sits inside the heading's row (`screen.getByRole("heading", { name: "Vectorize" }).parentElement`). `EmptyState.test.tsx`: `expect(screen.getByRole("heading", { name: "Turn images into clean vectors" })).toBeInTheDocument();`. `RedrawSection.test.tsx`: find the test that looks for "Show Original" / "Revert" in `RedrawSection` and move those expectations to a new test rendering `RedrawChip` with the same item; `RedrawSection` with an active redraw must not render them.

- [ ] **Step 2: Run** — `npx vitest run src/components/TitleBar.test.tsx src/components/VectorizePanel.test.tsx src/components/EmptyState.test.tsx src/components/RedrawSection.test.tsx`. Expected: fail.

- [ ] **Step 3: Implement.**

`TitleBar.tsx`: delete the `<p>` tagline and the wrapping `leading-tight` div's second child; the `h1` stays.

`RedrawSection.tsx`: lift the `r.active && (…)` block into

```tsx
/** The chip on a redrawn image, with Show Original and Revert: the inspector's header wears it. */
export function RedrawChip({ item, onShowOriginal, onRevert }: { item: ImageItem; onShowOriginal: () => void; onRevert: () => void }) {
  if (!redrawOf(item).active) return null;
  return (
    <span className="flex items-center gap-1">
      <span className="mac-tint inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium">
        <span className="mac-dot" aria-hidden="true" />
        AI redraw
      </span>
      <button type="button" className="mac-ghost h-7 px-2 text-[12px]" onClick={onShowOriginal}>Show Original</button>
      <button type="button" className="mac-ghost h-7 px-2 text-[12px]" onClick={onRevert}>Revert</button>
    </span>
  );
}
```

`RedrawSectionProps` loses `onShowOriginal` and `onRevert`; the section renders the hint, the button and the failure only.

`VectorizePanel.tsx` header:

```tsx
      <div className="flex min-h-[44px] items-center justify-between gap-2 px-4 pb-2 pt-3">
        <h2 className="text-[15px] font-semibold">Vectorize</h2>
        {redrawChip}
      </div>
```

with `redrawChip?: ReactNode` in the props; `Wand2` import goes.

`App.tsx`: `redrawChip={CAN_REDRAW ? <RedrawChip item={item} onShowOriginal={() => setReviewing(item.image.id)} onRevert={() => revert(item.image.id)} /> : undefined}` and drop those two props from `<RedrawSection>`.

`EmptyState.tsx`: above the drop zone, `<h2 className="font-brand text-[22px] font-semibold tracking-tight">Turn images into clean vectors</h2>` as the first child of the column (the gap is already 8).

- [ ] **Step 4: Run everything** — `npm run test:run && npm run build`. Expected: pass.

- [ ] **Step 5: Commit** — `git commit -am "frontend: the chrome diet: no tagline in the title bar, a plain Vectorize header with the redraw chip, the tagline on the empty state"`.

---

### Task 8: Verification in the app, both appearances

**Files:**
- None to change unless the screenshots show a defect.

- [ ] **Step 1: Full suites** — `cd frontend && npm run test:run && npm run build && cd .. && cargo test -p studi0trace-desktop --release`.
- [ ] **Step 2: Run the web harness** in the browser pane (`.claude/launch.json` in `frontend/`, `npm run dev` on port 5173; it needs the Python server for a trace — `cd backend && .venv/bin/uvicorn studi0trace.main:app --port 8000`, check `frontend/vite.config.ts` for the proxy) or the Mac app (`cd apps/desktop && npm run dev`). Screenshot: the empty state; an image untraced (the ghost, whole); tracing (the sweep, the counting pill, the button's sweep); traced (split with the divider); a failure; the Auto card before and after a run; "Choose a style" open; the sidebar with a traced, a stale and a tracing image; the export menu with PDF. Toggle `.dark` on `<html>` (or Settings ▸ Appearance) and repeat the canvas and the inspector in light mode.
- [ ] **Step 3:** Export a PDF from the Mac app and open it in Preview: a vector (zoom stays crisp), the gradient present.
- [ ] **Step 4:** Fix what the screenshots show, with a test where behaviour changed; commit.
