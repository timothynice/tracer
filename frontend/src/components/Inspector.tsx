import { CircleDot, Eye, EyeOff, PenTool, Sparkles } from "lucide-react";
import { useMemo } from "react";

import { formatBytes, formatInt, formatMs } from "@/lib/format";
import { shapeLabel, type SvgDoc } from "@/lib/svgdoc";
import { Slider } from "./Slider";

export interface InspectorState {
  open: boolean;
  points: boolean;
  outlines: boolean;
  hidden: ReadonlySet<number>;
  highlight: number | null;
  minArea: number;
}

export const EMPTY_INSPECTOR: InspectorState = { open: false, points: false, outlines: false, hidden: new Set(), highlight: null, minArea: 0 };

export interface InspectorProps {
  /** The vector on screen; null before a trace, when only the rail shows, off. */
  doc: SvgDoc | null;
  bytes: number;
  /** Engine time for this trace, in ms. */
  elapsedMs?: number | null;
  /** True once the cleanup has changed what will be exported. */
  edited?: boolean;
  state: InspectorState;
  onChange: (patch: Partial<InspectorState>) => void;
  /** Hide or show one shape by hand. `state.hidden` also holds what the threshold drops, so the caller owns the manual set. */
  onToggle: (index: number) => void;
}

/** Shapes small enough to be specks, given the current threshold. */
export function tinyShapes(doc: SvgDoc, minArea: number): number[] {
  return minArea <= 0 ? [] : doc.shapes.filter((s) => s.area <= minArea).map((s) => s.index);
}

/** The sidebar's Inspect tab: the display rail, then the trace's numbers, the cleanup and the layers. */
export function Inspector({ doc, bytes, elapsedMs, edited, state, onChange, onToggle }: InspectorProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="group" aria-label="Display" className="grid grid-cols-2 gap-2 px-3 pb-3 pt-1">
        <button type="button" className="mac-toggle" aria-pressed={state.points} disabled={!doc} title="Show the anchor points of every shape" onClick={() => onChange({ points: !state.points })}>
          <CircleDot className="h-5 w-5" aria-hidden="true" />
          Anchor points
        </button>
        <button type="button" className="mac-toggle" aria-pressed={state.outlines} disabled={!doc} title="Outline every shape" onClick={() => onChange({ outlines: !state.outlines })}>
          <PenTool className="h-5 w-5" aria-hidden="true" />
          Outlines
        </button>
      </div>
      {doc ? <InspectorBody doc={doc} bytes={bytes} elapsedMs={elapsedMs} edited={edited} state={state} onChange={onChange} onToggle={onToggle} /> : <p className="border-t px-3 pt-4 text-center text-[12px] text-muted-foreground">Trace this image to inspect it.</p>}
    </div>
  );
}

function InspectorBody({ doc, bytes, elapsedMs, edited, state, onChange, onToggle }: InspectorProps & { doc: SvgDoc }) {
  // The summary describes what will be exported, so hiding a shape moves it.
  const live = useMemo(() => doc.shapes.filter((s) => !state.hidden.has(s.index)), [doc, state.hidden]);
  const totalAnchors = useMemo(() => live.reduce((n, s) => n + s.anchors.length, 0), [live]);
  const colours = useMemo(() => new Set(live.map((s) => s.fill)).size, [live]);
  const tiny = useMemo(() => tinyShapes(doc, state.minArea), [doc, state.minArea]);
  const maxArea = useMemo(() => Math.max(1, ...doc.shapes.map((s) => s.area)), [doc]);

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto border-t px-3 pb-3 pt-3">
      <dl className="tabular grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Shapes{edited ? " (edited)" : ""}</dt>
        <dd className="text-right font-medium">{formatInt(live.length)}</dd>
        <dt className="text-muted-foreground">Anchors</dt>
        <dd className="text-right font-medium">{formatInt(totalAnchors)}</dd>
        <dt className="text-muted-foreground">Colours</dt>
        <dd className="text-right font-medium">{formatInt(colours)}</dd>
        <dt className="text-muted-foreground">Size</dt>
        <dd className="text-right font-medium">{formatBytes(bytes)}</dd>
        <dt className="text-muted-foreground">Time</dt>
        <dd className="text-right font-medium">{formatMs(elapsedMs)}</dd>
      </dl>

      <section className="space-y-2 border-t pt-3">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Clean up</h4>
        <p className="text-[11px] leading-snug text-muted-foreground">
          Tracing can leave stray one- or two-pixel shapes along edges and in noisy areas. Raise this to leave them out of the export.
        </p>
        <label className="block space-y-1 text-xs">
          <span className="flex items-center justify-between">
            <span>Drop specks under</span>
            <span className="tabular text-muted-foreground">{state.minArea ? `${formatInt(Math.round(state.minArea))} px²` : "off"}</span>
          </span>
          <Slider min={0} max={Math.round(maxArea / 20)} step={1} value={state.minArea} onValueChange={(v) => onChange({ minArea: v })} aria-label="Drop shapes smaller than" />
        </label>
        <p className="text-[11px] text-muted-foreground">
          {tiny.length ? `${tiny.length} shape${tiny.length === 1 ? "" : "s"} will be left out of the export.` : "Nothing dropped."}
        </p>
      </section>

      <section className="space-y-0.5 border-t pt-3">
        <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Layers</h4>
        <ul className="space-y-0.5">
          {doc.shapes.map((s) => {
            const off = state.hidden.has(s.index);
            const on = state.highlight === s.index;
            return (
              <li key={s.index}>
                <div
                  className={`flex items-center gap-2 rounded-sm px-1.5 py-1 text-xs transition-colors ${on ? "mac-tint" : "hover:bg-accent/60"}`}
                  onMouseEnter={() => onChange({ highlight: s.index })}
                  onMouseLeave={() => onChange({ highlight: null })}
                >
                  <span
                    aria-hidden="true"
                    className="checker h-3.5 w-3.5 shrink-0 rounded-[3px] border"
                    style={s.paint === "solid" ? { background: s.fill, opacity: s.opacity } : undefined}
                    title={s.paint === "gradient" ? "gradient" : s.fill}
                  />
                  <span className={`min-w-0 flex-1 truncate ${off ? "text-muted-foreground line-through" : ""}`}>{shapeLabel(s)}</span>
                  {s.filtered && <Sparkles className="h-3 w-3 shrink-0 text-brand" aria-label="has a filter" />}
                  <span className="tabular shrink-0 text-[11px] text-muted-foreground">{s.anchors.length}</span>
                  <button
                    type="button"
                    className="mac-icon h-6 w-6 shrink-0"
                    aria-label={`${off ? "Show" : "Hide"} ${shapeLabel(s)}`}
                    onClick={() => onToggle(s.index)}
                  >
                    {off ? <EyeOff className="h-3.5 w-3.5" aria-hidden="true" /> : <Eye className="h-3.5 w-3.5" aria-hidden="true" />}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

/** Half of the two-tone overlay pair. Fixed rather than themed — see below. */
const INK = "#0f172a";

/** The marks drawn over the canvas: outlines, anchors, and the highlighted shape. */
export function InspectorOverlay({ doc, state, scale }: { doc: SvgDoc; state: InspectorState; scale: number }) {
  if (!state.points && !state.outlines && state.highlight === null) return null;
  const r = 2.5 / Math.max(scale, 0.01);
  const w = 1 / Math.max(scale, 0.01);
  return (
    <svg
      viewBox={`0 0 ${doc.width} ${doc.height}`}
      width={doc.width}
      height={doc.height}
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      aria-hidden="true"
    >
      {doc.shapes.map((s) => {
        if (state.hidden.has(s.index)) return null;
        const lit = state.highlight === s.index;
        const [x, y, bw, bh] = s.bounds;
        return (
          <g key={s.index}>
            {(state.outlines || lit) && s.outline && (
              // The shape's own geometry, so curves stay curves — drawn as
              // interleaved two-tone dashes.
              //
              // Artwork can be any colour, so no single stroke colour is safe:
              // a dark line reads as ink over pale art (and gets reported as a
              // tracing bug, rightly), while a light one disappears over white.
              // Two offset dash phases in contrasting colours always leave one
              // visible, and dashes are never mistaken for a filled edge.
              //
              // The pair is fixed, not themed: how light the *artwork* is has
              // nothing to do with whether the app is in dark mode, and a themed
              // pair goes light-on-light the moment those disagree.
              <>
                <g
                  fill="none"
                  stroke={INK}
                  strokeWidth={(lit ? 2 : 1) * w}
                  strokeLinejoin="round"
                  strokeDasharray={`${4 * w} ${4 * w}`}
                  opacity={lit ? 0.9 : 0.7}
                  dangerouslySetInnerHTML={{ __html: s.outline }}
                />
                <g
                  fill="none"
                  stroke="hsl(var(--brand-accent))"
                  strokeWidth={(lit ? 2 : 1) * w}
                  strokeLinejoin="round"
                  strokeDasharray={`${4 * w} ${4 * w}`}
                  strokeDashoffset={4 * w}
                  opacity={lit ? 1 : 0.9}
                  dangerouslySetInnerHTML={{ __html: s.outline }}
                />
              </>
            )}
            {lit && <rect x={x} y={y} width={bw} height={bh} fill="none" stroke="hsl(var(--brand-accent))" strokeWidth={w} strokeDasharray={`${4 * w} ${3 * w}`} />}
            {(state.points || lit) &&
              s.anchors.map(([px, py], i) => (
                <circle key={i} cx={px} cy={py} r={r} fill="hsl(var(--brand-accent))" stroke={INK} strokeWidth={w} />
              ))}
          </g>
        );
      })}
    </svg>
  );
}
