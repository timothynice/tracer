import { AlertCircle, ChevronLeft, ChevronRight, Loader2, RotateCw, Wand2 } from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import type { ViewMode } from "@/platform/types";
import { ViewerToolbar, type Tool } from "./ViewerToolbar";

export interface ViewerHandle {
  zoomIn(): void;
  zoomOut(): void;
  actualSize(): void;
  fit(): void;
}

export interface ViewerProps {
  sourceUrl: string;
  svg: string | undefined;
  width: number;
  height: number;
  mode: ViewMode;
  onModeChange: (mode: ViewMode) => void;
  /** What the wait is for ("Queued…", "Tracing…", "Trying 4 styles…") and when it began; null when nothing runs. */
  busy: { phase: string; startedAt: number } | null;
  errorMessage?: string;
  onRetry?: () => void;
  display: { points: boolean; outlines: boolean };
  onDisplayChange: (patch: { points?: boolean; outlines?: boolean }) => void;
  layersOpen: boolean;
  onToggleLayers: () => void;
  /** Drawn in the vector's own coordinates, over everything. */
  marks?: (scale: number) => ReactNode;
  /** Rendered inside the viewport, e.g. the layer inspector. */
  panel?: ReactNode;
  /** The canvas's own Generate Vector button, shown over an untraced image. */
  onGenerate?: () => void;
  /** Raster against raster (the drift check): this image takes the vector's place, drawn in the source's frame, under `label`. */
  compare?: { url: string; label: string };
}

interface Transform {
  scale: number;
  x: number;
  y: number;
}

const MIN_SCALE = 0.05;
const MAX_SCALE = 32;
const STEP = 1.25;
const PAD = 32;
// The toolbar floats over the foot of the viewport (44 px tall, 12 px up from the edge): a fit leaves its space, plus a little air, below the image.
const BOTTOM_INSET = 44 + 12 + 8;
// ... and the corner chips (12 px down, 22 px tall) stay clear of the top edge.
const TOP_INSET = 40;

// Space belongs to a text field, and to a control it would otherwise activate (preventing keydown cancels a button's click).
const keepsSpace = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(target.tagName) || !!target.closest('[role="tab"],[role="menuitem"],[role="button"],[role="option"],[role="checkbox"],[role="radio"],[role="switch"]'));

// Controls painted over the viewer (the layer inspector, the error card) keep their wheel and gesture events.
const overUi = (e: Event) => !!(e.target as Element | null)?.closest?.("[data-overlay-ui]");

export type Stage = "untraced" | "tracing" | "retracing" | "traced" | "failed";

/** What the canvas is showing: a trace or a redraw, or until then the source as a ghost. */
export function stageOf(p: { svg?: string; compare?: unknown; busy: unknown; errorMessage?: string }): Stage {
  if (p.svg || p.compare) return p.busy ? "retracing" : "traced";
  if (p.errorMessage) return "failed";
  return p.busy ? "tracing" : "untraced";
}

const reducedMotion = () => typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const WIPE = "transition-[clip-path] duration-[450ms] delay-150 ease-out";

export const Viewer = forwardRef<ViewerHandle, ViewerProps>(function Viewer(props, ref) {
  const { sourceUrl, svg, width, height, mode, onModeChange, busy, errorMessage, onRetry, display, onDisplayChange, layersOpen, onToggleLayers, marks, panel, compare, onGenerate } = props;
  const [split, setSplit] = useState(0.5);
  const [overlay, setOverlay] = useState(0.7);
  const [tool, setTool] = useState<Tool>("pan");
  const [spaceHeld, setSpaceHeld] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  // true once the user has zoomed or panned since the last fit; a resize refits only while it is false
  const touched = useRef(false);

  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // the pane an image occupies: half the viewport side by side
  const paneW = mode === "side" ? size.w / 2 : size.w;
  const fitTransform = useCallback((): Transform => {
    if (!paneW || !size.h || !width || !height) return { scale: 1, x: 0, y: 0 };
    const availH = Math.max(1, size.h - TOP_INSET - BOTTOM_INSET);
    // a tiny image fits above the zoom cap, and Zoom In would then zoom out
    const scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, (paneW - PAD * 2) / width, availH / height));
    return { scale, x: (paneW - width * scale) / 2, y: TOP_INSET + (availH - height * scale) / 2 };
  }, [paneW, size.h, width, height]);

  useLayoutEffect(() => {
    touched.current = false;
  }, [sourceUrl, mode, width, height]);
  useEffect(() => {
    if (!touched.current && paneW && size.h && width && height) setT(fitTransform());
  }, [sourceUrl, mode, paneW, size.h, width, height, fitTransform]);

  const zoomAt = useCallback((factor: number, cx: number, cy: number) => {
    touched.current = true;
    setT((prev) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      const k = scale / prev.scale;
      return { scale, x: cx - (cx - prev.x) * k, y: cy - (cy - prev.y) * k };
    });
  }, []);
  const zoomTo = useCallback(
    (scale: number | "fit") => {
      touched.current = scale !== "fit";
      setT(scale === "fit" ? fitTransform() : { scale, x: (paneW - width * scale) / 2, y: TOP_INSET + (Math.max(1, size.h - TOP_INSET - BOTTOM_INSET) - height * scale) / 2 });
    },
    [fitTransform, paneW, size.h, width, height],
  );

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => zoomAt(STEP, paneW / 2, size.h / 2),
      zoomOut: () => zoomAt(1 / STEP, paneW / 2, size.h / 2),
      actualSize: () => zoomTo(1),
      fit: () => zoomTo("fit"),
    }),
    [zoomAt, zoomTo, paneW, size.h],
  );

  // The trackpad: two-finger scroll pans; a pinch zooms (ctrl+wheel in Chromium, gesture events in WebKit).
  // Added by hand because React's wheel listener is passive and cannot stop the page zooming.
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const local = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return [(e.clientX - r.left) % (paneW || 1), e.clientY - r.top] as const;
    };
    const wheel = (e: WheelEvent) => {
      if (overUi(e)) return;
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const [cx, cy] = local(e);
        zoomAt(Math.exp(-e.deltaY * 0.01), cx, cy);
      } else {
        touched.current = true;
        setT((p) => ({ ...p, x: p.x - e.deltaX, y: p.y - e.deltaY }));
      }
    };
    let last = 1;
    const gestureStart = (e: Event) => {
      if (overUi(e)) return;
      e.preventDefault();
      last = 1;
    };
    const gestureChange = (e: Event) => {
      if (overUi(e)) return;
      e.preventDefault();
      const g = e as Event & { scale: number; clientX: number; clientY: number };
      const [cx, cy] = local(g);
      zoomAt(g.scale / last, cx, cy);
      last = g.scale;
    };
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("gesturestart", gestureStart);
    el.addEventListener("gesturechange", gestureChange);
    return () => {
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("gesturestart", gestureStart);
      el.removeEventListener("gesturechange", gestureChange);
    };
  }, [paneW, zoomAt]);

  // holding Space is the hand, whatever the tool
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !e.metaKey && !e.ctrlKey && !e.altKey && !keepsSpace(e.target)) {
        e.preventDefault();
        setSpaceHeld(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceHeld(false);
    };
    // a keyup the window never sees (focus left, the tab hid) must not leave the hand stuck on
    const release = () => setSpaceHeld(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", release);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", release);
    };
  }, []);
  const panning = tool === "pan" || spaceHeld;

  const drag = useRef<{ x: number; y: number; tx: number; ty: number; kind: "pan" | "split" } | null>(null);
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    // controls painted over the viewer keep their clicks: capturing the pointer here would retarget them
    if ((e.target as HTMLElement).closest("[data-overlay-ui]")) return;
    if (!panning) {
      const r = viewport.current!.getBoundingClientRect();
      zoomAt(e.altKey ? 0.5 : 2, (e.clientX - r.left) % (paneW || 1), e.clientY - r.top);
      return;
    }
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "pan" };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "split") {
      const rect = viewport.current!.getBoundingClientRect();
      setSplit(Math.min(0.98, Math.max(0.02, (e.clientX - rect.left) / (rect.width || 1))));
    } else {
      touched.current = true;
      setT((prev) => ({ ...prev, x: d.tx + (e.clientX - d.x), y: d.ty + (e.clientY - d.y) }));
    }
  };
  const onPointerUp = () => (drag.current = null);
  const startSplit = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "split" };
  };

  const imgStyle = useMemo(() => ({ width, height, transform: `translate(${t.x}px, ${t.y}px) scale(${t.scale})`, transformOrigin: "0 0" as const }), [t, width, height]);

  // the right-hand side: the vector, or in a drift check the redraw
  const shown = !!svg || !!compare;
  const rightLabel = compare?.label ?? "Vector";
  const stage = stageOf({ svg, compare, busy, errorMessage });
  const ghost = stage === "untraced" || stage === "tracing" || stage === "failed";

  // the pill's seconds
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!busy) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy]);
  const counting = busy && busy.phase !== "Queued…";
  const pill = busy ? (counting ? `${busy.phase} ${Math.max(0, Math.floor((now - busy.startedAt) / 1000))} s` : busy.phase) : null;

  // The reveal, once, when a vector (or a redraw) first arrives: "start" paints it clipped away, "run" lets it wipe in.
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
  const wiping = wipe === "run" ? WIPE : "";
  const arriving = wipe !== null ? "motion-fade [animation-delay:300ms]" : "";

  // the checkerboard is under the image only; the panel around it stays the panel
  const board = <div aria-hidden="true" className="checker absolute left-0 top-0 rounded-[2px] shadow-sm" style={imgStyle} />;
  const source = <img src={sourceUrl} alt="Source raster" draggable={false} className="absolute left-0 top-0 max-w-none select-none" style={{ ...imgStyle, imageRendering: t.scale > 3 ? "pixelated" : "auto" }} />;
  const marksNode = marks ? (
    <div className="pointer-events-none absolute left-0 top-0" style={imgStyle}>
      {marks(t.scale)}
    </div>
  ) : null;
  const vector = compare ? (
    <img src={compare.url} alt={compare.label} draggable={false} className="absolute left-0 top-0 max-w-none select-none" style={{ ...imgStyle, imageRendering: t.scale > 3 ? "pixelated" : "auto" }} />
  ) : svg ? (
    <div aria-label="Vector result" role="img" className={`absolute left-0 top-0 [&>svg]:block [&>svg]:h-full [&>svg]:w-full ${busy ? "opacity-60" : ""}`} style={imgStyle} dangerouslySetInnerHTML={{ __html: svg }} />
  ) : null;
  const sweep = stage === "tracing" ? <div data-testid="sweep" aria-hidden="true" className="sweep pointer-events-none absolute left-0 top-0" style={imgStyle} /> : null;
  const chip = (text: string, where: string) => <span className={`pointer-events-none absolute top-3 z-20 rounded-full bg-popover/85 px-2.5 py-0.5 text-[11px] font-medium shadow-sm backdrop-blur ${where} ${arriving}`}>{text}</span>;

  return (
    <section aria-label="Canvas" className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      {busy && shown && (
        <div className="absolute inset-x-0 top-0 z-30 h-0.5 overflow-hidden bg-muted" role="progressbar" aria-label="Tracing">
          <div className="h-full w-1/3 animate-[slide_1.1s_ease-in-out_infinite]" style={{ background: "var(--accent-mac)" }} />
        </div>
      )}
      <div
        ref={viewport}
        data-testid="viewport"
        data-mode={mode}
        className={`relative min-h-[16rem] flex-1 touch-none select-none overflow-hidden ${panning ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in"}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {shown && mode === "side" ? (
          <>
            <div data-testid="source-pane" className="absolute inset-y-0 left-0 w-1/2 overflow-hidden">
              {board}
              {source}
            </div>
            <div className={`absolute inset-y-0 right-0 w-1/2 overflow-hidden border-l ${arriving}`}>
              {board}
              {vector}
              {marksNode}
            </div>
            {chip("Original", "left-1/4 -translate-x-1/2")}
            {chip(rightLabel, "left-3/4 -translate-x-1/2")}
          </>
        ) : (
          <>
            {board}
            {/* Until something is shown the source is the whole canvas, as a ghost. In split it is then clipped to its side: running under the vector's half it would read as part of the trace. */}
            {(ghost || mode !== "vector") && (
              <div data-testid="source-pane" className={`absolute inset-0 ${ghost ? "ghost" : ""} ${shown && mode === "split" ? wiping : ""}`} style={shown && mode === "split" ? { clipPath: `inset(0 ${wipe === "start" ? 0 : (1 - split) * 100}% 0 0)` } : undefined}>
                {source}
              </div>
            )}
            {sweep}
            {shown && mode === "split" && (
              <div className={`absolute inset-0 ${wiping}`} style={{ clipPath: `inset(0 0 0 ${wipe === "start" ? 100 : split * 100}%)` }}>
                {vector}
                {marksNode}
              </div>
            )}
            {shown && mode === "overlay" && (
              <>
                <div className={`absolute inset-0 ${arriving}`} style={{ opacity: overlay }}>
                  {vector}
                </div>
                {marksNode}
              </>
            )}
            {shown && mode === "vector" && (
              <div className={`absolute inset-0 ${arriving}`}>
                {vector}
                {marksNode}
              </div>
            )}
            {/* The divider and its handle wait for a vector: before that the centre of the viewport belongs to the pill and the error card. */}
            {shown && mode === "split" && (
              <div
                role="separator"
                aria-label="Comparison divider"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(split * 100)}
                aria-orientation="vertical"
                tabIndex={0}
                onPointerDown={startSplit}
                onKeyDown={(e) => {
                  if (e.key === "ArrowLeft") setSplit((s) => Math.max(0.02, s - 0.02));
                  if (e.key === "ArrowRight") setSplit((s) => Math.min(0.98, s + 0.02));
                }}
                className={`absolute inset-y-0 z-10 w-6 -translate-x-1/2 cursor-col-resize ${arriving}`}
                style={{ left: `${split * 100}%` }}
              >
                <div className="mx-auto h-full w-px bg-foreground/50" />
                <div className="absolute left-1/2 top-1/2 flex h-10 w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-popover/90 shadow-elevated backdrop-blur">
                  <ChevronLeft className="-mr-1 h-3.5 w-3.5" aria-hidden="true" />
                  <ChevronRight className="-ml-1 h-3.5 w-3.5" aria-hidden="true" />
                </div>
              </div>
            )}
            {shown && mode === "split" && chip("Original", "left-3")}
            {shown && mode === "split" && chip(rightLabel, "right-3")}
            {shown && mode === "overlay" && chip("Overlay", "left-1/2 -translate-x-1/2")}
            {shown && mode === "vector" && chip(rightLabel, "left-1/2 -translate-x-1/2")}
          </>
        )}

        {panel}

        {shown && errorMessage && (
          <div className="absolute inset-x-0 bottom-16 mx-auto w-fit max-w-md rounded-lg bg-destructive/90 px-3 py-2 text-[13px] text-destructive-foreground shadow-md" role="alert">
            {errorMessage}
          </div>
        )}

        {!shown && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center p-6">
            {errorMessage ? (
              <div role="alert" data-overlay-ui className="pointer-events-auto max-w-sm rounded-xl border bg-popover/95 p-4 text-center shadow-elevated backdrop-blur">
                <AlertCircle className="mx-auto h-5 w-5 text-destructive" aria-hidden="true" />
                <p className="mt-2 text-[13px] font-semibold">Tracing failed</p>
                <p className="mt-1 text-[13px] text-muted-foreground">{errorMessage}</p>
                {onRetry && (
                  <button type="button" className="mac-button mt-3" onClick={onRetry}>
                    <RotateCw className="h-4 w-4" aria-hidden="true" />
                    Try Again
                  </button>
                )}
              </div>
            ) : pill ? (
              <p className="flex items-center gap-2.5 rounded-full border bg-popover/90 px-3.5 py-1.5 text-[13px] font-medium shadow-sm backdrop-blur" aria-live="polite">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
                <span className="tabular">{pill}</span>
              </p>
            ) : (
              <button type="button" data-overlay-ui className="mac-primary pointer-events-auto w-auto px-4 shadow-elevated" onClick={onGenerate} title="Generate Vector (⌘↩)">
                <Wand2 className="h-4 w-4" aria-hidden="true" />
                Generate Vector
                <kbd className="ml-1 font-sans text-[11px] font-medium opacity-70" aria-hidden="true">
                  ⌘↩
                </kbd>
              </button>
            )}
          </div>
        )}
      </div>
      <ViewerToolbar
        tool={tool}
        onTool={setTool}
        scale={t.scale}
        onZoomTo={zoomTo}
        mode={mode}
        onMode={onModeChange}
        overlay={overlay}
        onOverlay={setOverlay}
        hasVector={!!svg && !compare}
        rightLabel={rightLabel}
        display={display}
        onDisplayChange={onDisplayChange}
        layersOpen={layersOpen}
        onToggleLayers={onToggleLayers}
      />
    </section>
  );
});
