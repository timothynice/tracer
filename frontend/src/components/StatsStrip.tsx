import { AlertCircle } from "lucide-react";

import type { EngineResult } from "@/lib/api";
import { formatBytes, formatInt, formatMs } from "@/lib/format";

export interface StatsStripProps {
  engineLabel: string;
  result: EngineResult | undefined;
  updating?: boolean;
  /** Set once the inspector has changed what will be exported. */
  edited?: { paths: number; bytes: number };
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="tabular text-sm font-medium">{value}</span>
    </div>
  );
}

export function StatsStrip({ engineLabel, result, updating, edited }: StatsStripProps) {
  if (result?.error) {
    return (
      <div role="alert" className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          <strong className="font-medium">{engineLabel} failed:</strong> {result.error.message}
        </span>
      </div>
    );
  }
  const s = result?.stats;
  return (
    <div className={`flex flex-wrap items-center gap-x-6 gap-y-2 transition-opacity ${updating ? "opacity-60" : ""}`} aria-live="polite">
      <span className="pill">
        <span className="dot-brand" aria-hidden="true" />
        {engineLabel}
      </span>
      <Stat label="Paths" value={formatInt(edited ? edited.paths : s?.paths)} />
      <Stat label="Nodes" value={formatInt(s?.nodes)} />
      <Stat label="Colours" value={formatInt(s?.unique_fills)} />
      <Stat label="Size" value={formatBytes(edited ? edited.bytes : s?.bytes)} />
      {edited && (
        <span className="pill" title="The inspector has changed what will be exported">
          <span className="dot-brand" aria-hidden="true" />
          edited
        </span>
      )}
      <Stat label="Time" value={formatMs(result?.elapsed_ms)} />
    </div>
  );
}
