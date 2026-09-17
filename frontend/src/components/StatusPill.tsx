import type { HealthState } from "@/hooks/useHealth";

export function StatusPill({ status, health, attempts }: Pick<HealthState, "status" | "health" | "attempts">) {
  if (status === "ok" && health) {
    return (
      <span className="pill" title={`Backend v${health.version}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
        <span className="hidden sm:inline">v{health.version} ·</span> {health.engines.join(", ")}
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
