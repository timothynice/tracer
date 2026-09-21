import type { HealthState } from "@/hooks/useHealth";

export function StatusPill({ status, health, attempts }: Pick<HealthState, "status" | "health" | "attempts">) {
  if (status === "ok" && health) {
    // The Vexel backend is worth showing: both implementations produce the same
    // SVG, but the Python fallback is ten times slower, so "which one am I
    // talking to" is otherwise only answerable with a stopwatch.
    const engine = health.vexel ? ` · vexel ${health.vexel}` : "";
    return (
      <span className="pill" title={`Backend v${health.version}${engine}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
        Connected <span className="hidden sm:inline">· v{health.version}{engine}</span>
      </span>
    );
  }
  if (status === "down") {
    return (
      <span className="pill" role="status">
        <span className="h-1.5 w-1.5 rounded-full bg-destructive" aria-hidden="true" />
        Server unreachable · retrying
      </span>
    );
  }
  return (
    <span className="pill" role="status" aria-live="polite">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-warning" aria-hidden="true" />
      {status === "checking" ? "Connecting…" : `Waking server${attempts > 1 ? ` (${attempts})` : ""}…`}
    </span>
  );
}
