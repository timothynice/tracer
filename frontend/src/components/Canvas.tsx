import * as Tabs from "@radix-ui/react-tabs";
import { AlertCircle, CircleDot, Columns2, Layers2, Loader2, Maximize, Minus, PenTool, Plus, RotateCw, Spline, SplitSquareHorizontal } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type WheelEvent } from "react";

import { formatPercent } from "@/lib/format";

export type ViewMode = "split" | "side" | "overlay" | "vector";
const VIEW_KEY = "studi0trace.view";
const MODES: { value: ViewMode; label: string; icon: typeof Columns2 }[] = [
  { value: "split", label: "Split", icon: SplitSquareHorizontal },
  { value: "side", label: "Side by side", icon: Columns2 },
  { value: "overlay", label: "Overlay", icon: Layers2 },
  { value: "vector", label: "Vector", icon: Spline },
];

export interface CanvasProps {
  sourceUrl: string;
  svg: string | undefined;
  width: number;
  height: number;
  updating?: boolean;
  /** What the wait is for, e.g. "Uploading…" — shown while `updating` and no vector. */
  busyLabel?: string;
  errorMessage?: string;
  onRetry?: () => void;
  /** Display toggles owned by the toolbar: how the vector is drawn, not what it is. */
  display?: { points: boolean; outlines: boolean };
  onDisplayChange?: (patch: { points?: boolean; outlines?: boolean }) => void;
  /** Drawn in the vector's own coordinate space, on top of everything. */
  marks?: (scale: number) => ReactNode;
  /** Rendered inside the viewport, e.g. the inspector panel. */
  panel?: ReactNode;
}

interface Transform {
  scale: number;
  x: number;
  y: number;
}

const MIN_SCALE = 0.05;
const MAX_SCALE = 32;

export function Canvas({ sourceUrl, svg, width, height, updating, busyLabel, errorMessage, onRetry, marks, panel, display, onDisplayChange }: CanvasProps) {
  const [mode, setMode] = useState<ViewMode>(() => (localStorage.getItem(VIEW_KEY) as ViewMode) || "split");
  const [split, setSplit] = useState(0.5);
  const [overlay, setOverlay] = useState(0.7);
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [fitted, setFitted] = useState(false);

  const pickMode = (m: string) => {
    setMode(m as ViewMode);
    localStorage.setItem(VIEW_KEY, m);
  };

  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // The pane an image occupies: half the viewport in side-by-side.
  const paneW = mode === "side" ? size.w / 2 : size.w;
  const fitTransform = useCallback((): Transform => {
    if (!paneW || !size.h || !width || !height) return { scale: 1, x: 0, y: 0 };
    const pad = 24;
    const scale = Math.min((paneW - pad * 2) / width, (size.h - pad * 2) / height);
    return { scale, x: (paneW - width * scale) / 2, y: (size.h - height * scale) / 2 };
  }, [paneW, size.h, width, height]);

  useEffect(() => {
    if (!fitted && paneW && size.h && width && height) {
      setT(fitTransform());
      setFitted(true);
    }
  }, [fitted, paneW, size.h, width, height, fitTransform]);
  // Dimensions arrive after the preview does, so a size change refits too.
  useEffect(() => setFitted(false), [sourceUrl, mode, width, height]);

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      setT((prev) => {
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
        const k = scale / prev.scale;
        return { scale, x: cx - (cx - prev.x) * k, y: cy - (cy - prev.y) * k };
      });
    },
    [],
  );

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const rect = viewport.current!.getBoundingClientRect();
    const cx = (e.clientX - rect.left) % paneW;
    zoomAt(Math.exp(-e.deltaY * 0.0015), cx, e.clientY - rect.top);
  };

  const drag = useRef<{ x: number; y: number; tx: number; ty: number; kind: "pan" | "split" } | null>(null);
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    // Controls painted over the canvas keep their own clicks: capturing the
    // pointer here retargets the following click to the viewport, which is how
    // a Retry button ends up doing nothing at all.
    if ((e.target as HTMLElement).closest("[data-overlay-ui]")) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "pan" };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "split") {
      const rect = viewport.current!.getBoundingClientRect();
      setSplit(Math.min(0.98, Math.max(0.02, (e.clientX - rect.left) / rect.width)));
    } else {
      setT((prev) => ({ ...prev, x: d.tx + (e.clientX - d.x), y: d.ty + (e.clientY - d.y) }));
    }
  };
  const onPointerUp = () => (drag.current = null);
  const startSplit = (e: ReactPointerEvent) => {
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "split" };
  };

  const imgStyle = useMemo(
    () => ({ width, height, transform: `translate(${t.x}px, ${t.y}px) scale(${t.scale})`, transformOrigin: "0 0" as const }),
    [t, width, height],
  );

  const source = <img src={sourceUrl} alt="Source raster" draggable={false} className="absolute left-0 top-0 max-w-none select-none" style={{ ...imgStyle, imageRendering: t.scale > 3 ? "pixelated" : "auto" }} />;
  const marksNode = marks ? (
    <div className="pointer-events-none absolute left-0 top-0" style={imgStyle}>
      {marks(t.scale)}
    </div>
  ) : null;
  const vector = svg ? (
    <div
      aria-label="Vector result"
      role="img"
      className={`absolute left-0 top-0 motion-fade [&>svg]:block [&>svg]:h-full [&>svg]:w-full ${updating ? "opacity-60" : ""}`}
      style={imgStyle}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  ) : null;

  return (
    <section aria-label="Canvas" className="card flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <Tabs.Root value={mode} onValueChange={pickMode}>
          <Tabs.List aria-label="View mode" className="inline-flex h-9 rounded-md bg-muted p-1">
            {MODES.map((m) => (
              <Tabs.Trigger
                key={m.value}
                value={m.value}
                className="inline-flex items-center gap-1.5 rounded-sm px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
              >
                <m.icon className="h-4 w-4" aria-hidden="true" />
                <span className="hidden md:inline">{m.label}</span>
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </Tabs.Root>

        {mode === "overlay" && (
          <label className="ml-2 flex items-center gap-2 text-xs text-muted-foreground">
            Vector opacity
            <input type="range" min={0} max={1} step={0.05} value={overlay} onChange={(e) => setOverlay(Number(e.target.value))} className="w-24 accent-[hsl(var(--primary))]" aria-label="Vector opacity" />
          </label>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {display && onDisplayChange && (
            <div className="inline-flex h-9 items-center rounded-md bg-muted p-1" role="group" aria-label="Show">
              <button
                type="button"
                aria-pressed={display.points}
                aria-label="Show anchor points"
                title="Anchor points"
                onClick={() => onDisplayChange({ points: !display.points })}
                className={`inline-flex h-7 w-7 items-center justify-center rounded-sm transition-colors ${display.points ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                <CircleDot className="h-4 w-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-pressed={display.outlines}
                aria-label="Show outlines"
                title="Outlines"
                onClick={() => onDisplayChange({ outlines: !display.outlines })}
                className={`inline-flex h-7 w-7 items-center justify-center rounded-sm transition-colors ${display.outlines ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                <PenTool className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}

          <div className="inline-flex h-9 items-center rounded-md bg-muted p-1" role="group" aria-label="Zoom">
            <button type="button" className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.25, paneW / 2, size.h / 2)}>
              <Minus className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="tabular inline-flex h-7 min-w-[3.25rem] items-center justify-center rounded-sm px-1 text-xs font-medium transition-colors hover:bg-background" aria-label="Zoom to 100%" onClick={() => setT({ scale: 1, x: (paneW - width) / 2, y: (size.h - height) / 2 })}>
              {formatPercent(t.scale)}
            </button>
            <button type="button" className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground" aria-label="Zoom in" onClick={() => zoomAt(1.25, paneW / 2, size.h / 2)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground" aria-label="Fit to view" onClick={() => setT(fitTransform())}>
              <Maximize className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {updating && <div className="h-0.5 w-full overflow-hidden bg-muted" role="progressbar" aria-label="Tracing"><div className="h-full w-1/3 animate-[slide_1.1s_ease-in-out_infinite] bg-primary-foreground" /></div>}

      <div
        ref={viewport}
        data-mode={mode}
        className="checker relative min-h-[16rem] flex-1 cursor-grab touch-none select-none overflow-hidden active:cursor-grabbing"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {mode === "side" ? (
          <>
            <div data-testid="source-pane" className="absolute inset-y-0 left-0 w-1/2 overflow-hidden">{source}</div>
            <div className="absolute inset-y-0 right-0 w-1/2 overflow-hidden border-l">{vector}{marksNode}</div>
            <span className="pointer-events-none absolute bottom-2 left-2 pill">Source</span>
            <span className="pointer-events-none absolute bottom-2 right-2 pill">Vector</span>
          </>
        ) : (
          <>
            {/* In split the source is clipped to its own side. Letting it run
                under the empty half would show the raster where the vector
                belongs, and read as a finished trace. */}
            {mode === "split" ? (
              <div data-testid="source-pane" className="absolute inset-0" style={{ clipPath: `inset(0 ${(1 - split) * 100}% 0 0)` }}>
                {source}
              </div>
            ) : (
              mode !== "vector" && source
            )}
            {mode === "split" && (
              <div className="absolute inset-0" style={{ clipPath: `inset(0 0 0 ${split * 100}%)` }}>
                {vector}
                {marksNode}
              </div>
            )}
            {mode === "overlay" && (
              <>
                <div className="absolute inset-0" style={{ opacity: overlay }}>{vector}</div>
                {marksNode}
              </>
            )}
            {mode === "vector" && (
              <>
                {vector}
                {marksNode}
              </>
            )}
            {mode === "split" && (
              <>
                <div
                  role="separator"
                  aria-label="Comparison divider"
                  aria-valuenow={Math.round(split * 100)}
                  aria-orientation="vertical"
                  tabIndex={0}
                  onPointerDown={startSplit}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowLeft") setSplit((s) => Math.max(0.02, s - 0.02));
                    if (e.key === "ArrowRight") setSplit((s) => Math.min(0.98, s + 0.02));
                  }}
                  className="absolute inset-y-0 z-10 w-4 -translate-x-1/2 cursor-col-resize focus-visible:outline-none"
                  style={{ left: `${split * 100}%` }}
                >
                  <div className="mx-auto h-full w-0.5 bg-foreground/70" />
                  <div className="absolute left-1/2 top-1/2 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background shadow-sm">
                    <SplitSquareHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                  </div>
                </div>
                <span className="pointer-events-none absolute bottom-2 left-2 pill">Source</span>
                <span className="pointer-events-none absolute bottom-2 right-2 pill">Vector</span>
              </>
            )}
          </>
        )}

        {panel}

        {svg && errorMessage && (
          <div className="absolute inset-x-0 bottom-0 m-3 rounded-md bg-destructive/90 px-3 py-2 text-sm text-destructive-foreground shadow-md" role="alert">
            {errorMessage}
          </div>
        )}

        {!svg && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center p-6">
            {errorMessage ? (
              <div role="alert" data-overlay-ui className="motion-rise pointer-events-auto max-w-sm rounded-lg border bg-card/95 p-4 text-center shadow-elevated backdrop-blur">
                <AlertCircle className="mx-auto h-5 w-5 text-destructive" aria-hidden="true" />
                <p className="mt-2 text-sm font-medium">Tracing failed</p>
                <p className="mt-1 text-sm text-muted-foreground">{errorMessage}</p>
                {onRetry && (
                  <button type="button" className="btn-secondary btn-sm mt-3" onClick={onRetry}>
                    <RotateCw className="h-4 w-4" aria-hidden="true" />
                    Try again
                  </button>
                )}
              </div>
            ) : updating ? (
              <div className="motion-fade flex items-center gap-2.5 rounded-full border bg-card/90 px-4 py-2 shadow-sm backdrop-blur" aria-live="polite">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
                <p className="text-sm font-medium">{busyLabel ?? "Tracing…"}</p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No vector yet</p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
