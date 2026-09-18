import { useCallback, useEffect, useMemo, useState } from "react";

import type { EngineDescription, ParamValues } from "@/lib/api";
import { normalizeValues, specsFor, type ParamSpec } from "@/lib/schema";

const KEY = (engine: string) => `studi0trace.params.${engine}`;

function load(engine: string): ParamValues | undefined {
  try {
    const raw = localStorage.getItem(KEY(engine));
    return raw ? (JSON.parse(raw) as ParamValues) : undefined;
  } catch {
    return undefined;
  }
}

function save(engine: string, values: ParamValues): void {
  try {
    localStorage.setItem(KEY(engine), JSON.stringify(values));
  } catch {
    /* ignore */
  }
}

export interface ParamsState {
  values: Record<string, ParamValues>;
  specs: Record<string, ParamSpec[]>;
  set: (engine: string, name: string, value: unknown) => void;
  /** Apply a preset: its keys over the engine's defaults, not over current values. */
  apply: (engine: string, params: ParamValues) => void;
  reset: (engine: string) => void;
}

/** Per-engine parameter values, defaulted from schema and persisted per engine. */
export function useParams(engines: EngineDescription[] | undefined): ParamsState {
  const specs = useMemo(() => Object.fromEntries((engines ?? []).map((e) => [e.id, specsFor(e)])), [engines]);
  const [values, setValues] = useState<Record<string, ParamValues>>({});

  // (Re)initialise whenever the engine list changes: keep current values where the engine still exists.
  useEffect(() => {
    if (!engines) return;
    setValues((prev) => {
      const next: Record<string, ParamValues> = {};
      for (const e of engines) next[e.id] = normalizeValues(specs[e.id], prev[e.id] ?? load(e.id));
      return next;
    });
  }, [engines, specs]);

  const set = useCallback(
    (engine: string, name: string, value: unknown) => {
      setValues((prev) => {
        const next = { ...prev, [engine]: normalizeValues(specs[engine] ?? [], { ...prev[engine], [name]: value }) };
        save(engine, next[engine]);
        return next;
      });
    },
    [specs],
  );

  const apply = useCallback(
    (engine: string, params: ParamValues) => {
      setValues((prev) => {
        const base = normalizeValues(specs[engine] ?? [], undefined);
        const next = { ...prev, [engine]: normalizeValues(specs[engine] ?? [], { ...base, ...params }) };
        save(engine, next[engine]);
        return next;
      });
    },
    [specs],
  );

  const reset = useCallback(
    (engine: string) => {
      setValues((prev) => {
        const next = { ...prev, [engine]: normalizeValues(specs[engine] ?? [], undefined) };
        save(engine, next[engine]);
        return next;
      });
    },
    [specs],
  );

  return { values, specs, set, apply, reset };
}
