import { useCallback, useEffect, useRef, useState } from "react";

import { getHealth, type Health } from "@/lib/api";

export type HealthStatus = "checking" | "waking" | "ok" | "down";

export interface HealthState {
  status: HealthStatus;
  health: Health | null;
  attempts: number;
  refresh: () => void;
}

export interface HealthOptions {
  /** ms between retries while waking (default 5000) */
  retryMs?: number;
  /** attempts before we call it "down" and slow the polling (default 24 ≈ 2 min) */
  giveUpAfter?: number;
  /** ms between retries once "down" (default 15000) */
  slowRetryMs?: number;
  timeoutMs?: number;
}

/** Polls /health until the backend answers. Free-tier hosts sleep; this is the wake-up loop. */
export function useHealth(opts: HealthOptions = {}): HealthState {
  const { retryMs = 5000, giveUpAfter = 24, slowRetryMs = 15000, timeoutMs = 4000 } = opts;
  const [status, setStatus] = useState<HealthStatus>("checking");
  const [health, setHealth] = useState<Health | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    let count = 0;

    const tick = async () => {
      try {
        const h = await getHealth(controller.signal, timeoutMs);
        if (!alive.current) return;
        setHealth(h);
        setStatus("ok");
      } catch (err) {
        if (!alive.current || (err as Error).name === "AbortError") return;
        count += 1;
        setAttempts(count);
        const down = count >= giveUpAfter;
        setStatus(down ? "down" : "waking");
        timer = setTimeout(tick, down ? slowRetryMs : retryMs);
      }
    };
    void tick();

    return () => {
      alive.current = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [nonce, retryMs, giveUpAfter, slowRetryMs, timeoutMs]);

  return { status, health, attempts, refresh };
}
