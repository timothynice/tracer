import { ChevronRight, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { Preset } from "@/lib/api";
import { formatBytes, formatInt, formatMs } from "@/lib/format";
import type { ParamSpec } from "@/lib/schema";
import { svgStats } from "@/lib/svgdoc";
import { shownAnswer, type Catalog, type ImageItem } from "@/state/library";
import { ParamPanel } from "./ParamPanel";
import { PresetCards } from "./PresetCards";
import { TraceButton } from "./TraceButton";

export interface VectorizePanelProps {
  item: ImageItem;
  catalog: Catalog;
  specs: ParamSpec[];
  invalidField: string | null;
  onPick: (preset: Preset) => void;
  onParam: (name: string, value: unknown) => void;
  onGenerate: () => void;
  onCancel: () => void;
  /** The export button and its menu. */
  exportMenu: ReactNode;
  /** AI redraw's section (the Mac app). */
  redraw?: ReactNode;
}

export function VectorizePanel({ item, catalog, specs, invalidField, onPick, onParam, onGenerate, onCancel, exportMenu, redraw }: VectorizePanelProps) {
  const [advanced, setAdvanced] = useState(invalidField !== null);
  useEffect(() => {
    if (invalidField !== null) setAdvanced(true); // a field the server refused is in there
  }, [invalidField]);
  const answer = shownAnswer(item);
  // counted from the markup, which holds shapes a path tally leaves out (copies, circles, rects); the server's tally stands in for markup that will not parse
  const counts = useMemo(() => (answer ? (svgStats(answer.svg) ?? { shapes: answer.stats.paths, nodes: answer.stats.nodes }) : null), [answer]);
  const candidates = catalog.presets.filter((p) => p.auto_candidate).length;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-start gap-3 p-4 pb-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-background ring-1 ring-border">
          <Wand2 className="h-5 w-5" aria-hidden="true" />
        </span>
        <div>
          <h2 className="text-[15px] font-semibold">Vectorize</h2>
          <p className="text-[12px] leading-snug text-muted-foreground">Convert your image to a clean, scalable vector.</p>
        </div>
      </div>
      {redraw && <div className="px-3 pb-3">{redraw}</div>}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3">
        <PresetCards
          presets={catalog.presets}
          defaults={catalog.engine.defaults}
          values={item.params}
          active={item.preset}
          auto={item.auto}
          autoRunning={item.job?.key === "auto"}
          onPick={onPick}
        />
        <div className="border-t pt-2">
          <button type="button" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)} className="mac-ghost h-8 w-full px-1.5 text-foreground">
            <ChevronRight className={`h-4 w-4 transition-transform ${advanced ? "rotate-90" : ""}`} aria-hidden="true" />
            Advanced Options
          </button>
          {advanced && (
            <div className="pt-1">
              <ParamPanel engine={catalog.engine.id} specs={specs} values={item.params} onChange={onParam} invalidField={invalidField} />
            </div>
          )}
        </div>
      </div>
      <div className="space-y-2 border-t p-3">
        {answer && (
          <p className="tabular text-center text-[11px] text-muted-foreground">
            {formatInt(counts!.shapes)} {counts!.shapes === 1 ? "shape" : "shapes"} · {formatInt(counts!.nodes)} {counts!.nodes === 1 ? "node" : "nodes"} · {formatBytes(answer.stats.bytes)} · {formatMs(answer.elapsedMs)}
          </p>
        )}
        <TraceButton item={item} candidates={candidates} onGenerate={onGenerate} onCancel={onCancel} />
        {exportMenu}
      </div>
    </div>
  );
}
