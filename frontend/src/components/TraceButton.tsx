import { Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { needsUpdate, traceKey, type ImageItem } from "@/state/library";

export interface TraceButtonProps {
  item: ImageItem;
  /** How many presets Auto tries, for "Trying 4 presets…". */
  candidates: number;
  onGenerate: () => void;
  onCancel: () => void;
}

export function TraceButton({ item, candidates, onGenerate, onCancel }: TraceButtonProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!item.job) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [item.job]);

  if (item.job) {
    const seconds = Math.max(0, Math.floor((now - item.job.startedAt) / 1000));
    const phase = item.job.phase === "queued" ? "Queued…" : item.job.key === "auto" ? `Trying ${candidates} presets…` : "Tracing…";
    return (
      <div className="space-y-1.5">
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground" aria-live="polite">
          <span className={item.job.phase === "queued" ? "inline-block h-1.5 w-1.5 rounded-full bg-warning" : "mac-dot animate-pulse"} aria-hidden="true" />
          {phase}
        </p>
        <button type="button" className="mac-button h-9 w-full" onClick={onCancel} title="Cancel Trace (⌘.)">
          Cancel · {seconds} s
        </button>
      </div>
    );
  }
  const current = item.shown !== null && item.shown === traceKey(item);
  const label = needsUpdate(item) ? "Update Vector" : "Generate Vector";
  return (
    <div className="space-y-1">
      <button type="button" className="mac-primary" disabled={current} onClick={onGenerate} title={`${label} (⌘↩)`}>
        <Wand2 className="h-4 w-4" aria-hidden="true" />
        {label}
      </button>
      {current && <p className="text-center text-[11px] text-muted-foreground">Up to date</p>}
    </div>
  );
}
