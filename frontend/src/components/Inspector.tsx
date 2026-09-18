import { Eye, EyeOff, Layers, PanelLeftClose, Sparkles, Undo2 } from "lucide-react";
import { useMemo } from "react";

import { formatBytes, formatInt } from "@/lib/format";
import { shapeLabel, type SvgDoc } from "@/lib/svgdoc";

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
  doc: SvgDoc;
  bytes: number;
  state: InspectorState;
  onChange: (patch: Partial<InspectorState>) => void;
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 py-1 text-xs">
      <span>{label}</span>
      <input type="checkbox" className="h-3.5 w-3.5 accent-[hsl(var(--primary))]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

/** Shapes small enough to be specks, given the current threshold. */
export function tinyShapes(doc: SvgDoc, minArea: number): number[] {
  return minArea <= 0 ? [] : doc.shapes.filter((s) => s.area <= minArea).map((s) => s.index);
}

export function Inspector({ doc, bytes, state, onChange }: InspectorProps) {
  // The summary describes what will be exported, so hiding a shape moves it.
  const live = useMemo(() => doc.shapes.filter((s) => !state.hidden.has(s.index)), [doc, state.hidden]);
  const totalAnchors = useMemo(() => live.reduce((n, s) => n + s.anchors.length, 0), [live]);
  const colours = useMemo(() => new Set(live.map((s) => s.fill)).size, [live]);
  const tiny = useMemo(() => tinyShapes(doc, state.minArea), [doc, state.minArea]);
  const maxArea = useMemo(() => Math.max(1, ...doc.shapes.map((s) => s.area)), [doc]);

  const toggleShape = (index: number) => {
    const next = new Set(state.hidden);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    onChange({ hidden: next });
  };

  return (
    <div
      data-overlay-ui
      className="motion-rise pointer-events-auto absolute left-3 top-3 z-30 flex max-h-[calc(100%-1.5rem)] w-[17rem] flex-col rounded-lg border bg-card/95 shadow-elevated backdrop-blur"
    >
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Layers className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="text-sm font-medium">Inspect</span>
        <button type="button" className="btn-ghost btn-icon ml-auto h-7 w-7" aria-label="Close inspector" onClick={() => onChange({ open: false, highlight: null })}>
          <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        <dl className="tabular grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Shapes</dt>
          <dd className="text-right font-medium">{formatInt(live.length)}</dd>
          <dt className="text-muted-foreground">Anchors</dt>
          <dd className="text-right font-medium">{formatInt(totalAnchors)}</dd>
          <dt className="text-muted-foreground">Colours</dt>
          <dd className="text-right font-medium">{formatInt(colours)}</dd>
          <dt className="text-muted-foreground">Size</dt>
          <dd className="text-right font-medium">{formatBytes(bytes)}</dd>
        </dl>

        <section className="space-y-1 border-t pt-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Show</h4>
          <Toggle label="Anchor points" checked={state.points} onChange={(v) => onChange({ points: v })} />
          <Toggle label="Outlines" checked={state.outlines} onChange={(v) => onChange({ outlines: v })} />
        </section>

        <section className="space-y-2 border-t pt-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Clean up</h4>
          <label className="block space-y-1 text-xs">
            <span className="flex items-center justify-between">
              <span>Drop specks under</span>
              <span className="tabular text-muted-foreground">{state.minArea ? `${formatInt(Math.round(state.minArea))} px²` : "off"}</span>
            </span>
            <input
              type="range"
              min={0}
              max={Math.round(maxArea / 20)}
              step={1}
              value={state.minArea}
              onChange={(e) => onChange({ minArea: Number(e.target.value) })}
              className="w-full accent-[hsl(var(--primary))]"
              aria-label="Drop shapes smaller than"
            />
          </label>
          <p className="text-[11px] text-muted-foreground">
            {tiny.length ? `${tiny.length} shape${tiny.length === 1 ? "" : "s"} will be left out of the export.` : "Nothing dropped."}
          </p>
          {state.hidden.size > 0 && (
            <button type="button" className="btn-ghost btn-sm -ml-3 text-xs text-muted-foreground" onClick={() => onChange({ hidden: new Set(), minArea: 0 })}>
              <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
              Restore all {state.hidden.size} hidden
            </button>
          )}
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
                    className={`flex items-center gap-2 rounded-sm px-1.5 py-1 text-xs transition-colors ${on ? "bg-accent ring-1 ring-primary/20" : "hover:bg-accent/60"}`}
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
                      className="btn-ghost btn-icon h-6 w-6 shrink-0"
                      aria-label={`${off ? "Show" : "Hide"} ${shapeLabel(s)}`}
                      onClick={() => toggleShape(s.index)}
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
    </div>
  );
}

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
            {(state.outlines || lit) && (
              <polyline
                points={s.anchors.map(([px, py]) => `${px},${py}`).join(" ")}
                fill="none"
                stroke={lit ? "hsl(var(--brand-accent))" : "hsl(var(--primary))"}
                strokeWidth={(lit ? 2 : 1) * w}
                strokeLinejoin="round"
                opacity={lit ? 1 : 0.55}
              />
            )}
            {lit && <rect x={x} y={y} width={bw} height={bh} fill="none" stroke="hsl(var(--brand-accent))" strokeWidth={w} strokeDasharray={`${4 * w} ${3 * w}`} />}
            {(state.points || lit) &&
              s.anchors.map(([px, py], i) => (
                <circle key={i} cx={px} cy={py} r={r} fill="hsl(var(--background))" stroke={lit ? "hsl(var(--brand-accent))" : "hsl(var(--primary))"} strokeWidth={w} />
              ))}
          </g>
        );
      })}
    </svg>
  );
}
