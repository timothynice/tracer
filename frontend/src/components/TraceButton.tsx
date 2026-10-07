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
        <span className="tabular relative" aria-live="polite">
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
