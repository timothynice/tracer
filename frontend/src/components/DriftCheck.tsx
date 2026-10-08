import { colourText, edgesText, VERDICT_LABEL, VERDICT_NOTE } from "@/lib/redraw";
import type { Drift, DriftVerdict, OpenedImage, ViewMode } from "@/platform/types";
import { Viewer } from "./Viewer";

const TONE: Record<DriftVerdict, { dot: string; tint: string }> = {
  close: { dot: "bg-success", tint: "bg-success/10" },
  noticeable: { dot: "bg-warning", tint: "bg-warning/10" },
  large: { dot: "bg-destructive", tint: "bg-destructive/10" },
};

/** The verdict as a dot of its tone on a tint of it: never a one-sided border. */
export function VerdictChip({ verdict }: { verdict: DriftVerdict }) {
  return (
    <span data-verdict={verdict} className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${TONE[verdict].tint}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${TONE[verdict].dot}`} aria-hidden="true" />
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

export interface DriftCheckProps {
  original: OpenedImage;
  redraw: OpenedImage;
  drift: Drift;
  mode: ViewMode;
  onModeChange: (mode: ViewMode) => void;
  /** The decision on a redraw waiting for one; absent when the redraw in use is only being looked at (Show Original). */
  decide?: { onUse: () => void; onTryAgain: () => void; onDiscard: () => void };
  onClose?: () => void;
}

/** Original against redraw, before anything changes: the Viewer comparing two rasters, the drift and the decision. */
export function DriftCheck({ original, redraw, drift, mode, onModeChange, decide, onClose }: DriftCheckProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="region" aria-label="Drift check" className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-2.5">
        <VerdictChip verdict={drift.verdict} />
        <p className="tabular flex gap-1.5 text-[12px] text-muted-foreground">
          <span>{edgesText(drift)}</span>
          <span aria-hidden="true">·</span>
          <span>{colourText(drift)}</span>
        </p>
        <p className="text-[12px] text-muted-foreground">{VERDICT_NOTE[drift.verdict]}</p>
        <div className="ml-auto flex items-center gap-2">
          {decide ? (
            <>
              <button type="button" className="mac-button" onClick={decide.onDiscard}>
                Discard
              </button>
              <button type="button" className="mac-button" onClick={decide.onTryAgain}>
                Try again
              </button>
              <button type="button" className="mac-primary h-8 w-auto px-4" onClick={decide.onUse}>
                Use redraw
              </button>
            </>
          ) : (
            <button type="button" className="mac-button" onClick={onClose}>
              Done
            </button>
          )}
        </div>
      </div>
      <Viewer
        sourceUrl={original.previewUrl}
        svg={undefined}
        compare={{ url: redraw.previewUrl, label: "AI redraw" }}
        width={original.width}
        height={original.height}
        mode={mode}
        onModeChange={onModeChange}
        busy={null}
        layersOpen={false}
        onToggleLayers={() => {}}
      />
    </div>
  );
}
