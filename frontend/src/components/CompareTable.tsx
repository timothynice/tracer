import type { EngineDescription, EngineResult } from "@/lib/api";
import { formatBytes, formatInt, formatMs } from "@/lib/format";

export interface CompareTableProps {
  engines: EngineDescription[];
  results: Record<string, EngineResult> | undefined;
  active: string;
  onPick: (id: string) => void;
  updating?: boolean;
}

/** Every engine's stats for the current image, so the user can pick the best one. */
export function CompareTable({ engines, results, active, onPick, updating }: CompareTableProps) {
  return (
    <div className={`card overflow-x-auto p-0 transition-opacity ${updating ? "opacity-60" : ""}`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="px-4 py-2 font-medium">Engine</th>
            <th className="px-4 py-2 text-right font-medium">Paths</th>
            <th className="px-4 py-2 text-right font-medium">Nodes</th>
            <th className="px-4 py-2 text-right font-medium">Colours</th>
            <th className="px-4 py-2 text-right font-medium">Size</th>
            <th className="px-4 py-2 text-right font-medium">Time</th>
          </tr>
        </thead>
        <tbody>
          {engines.map((e) => {
            const r = results?.[e.id];
            const isActive = e.id === active;
            return (
              <tr
                key={e.id}
                aria-selected={isActive}
                onClick={() => onPick(e.id)}
                className={`cursor-pointer border-t transition-colors hover:bg-accent/60 ${isActive ? "bg-accent/40" : ""}`}
              >
                <td className="px-4 py-2">
                  <span className="inline-flex items-center gap-2 font-medium">
                    <span className={`dot-brand ${isActive ? "" : "opacity-0"}`} aria-hidden="true" />
                    {e.label}
                  </span>
                  {r?.error && <span className="ml-2 text-xs text-destructive">{r.error.code}</span>}
                </td>
                <td className="tabular px-4 py-2 text-right">{formatInt(r?.stats?.paths)}</td>
                <td className="tabular px-4 py-2 text-right">{formatInt(r?.stats?.nodes)}</td>
                <td className="tabular px-4 py-2 text-right">{formatInt(r?.stats?.unique_fills)}</td>
                <td className="tabular px-4 py-2 text-right">{formatBytes(r?.stats?.bytes)}</td>
                <td className="tabular px-4 py-2 text-right">{formatMs(r?.elapsed_ms)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
