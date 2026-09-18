import { Check } from "lucide-react";

import type { ParamValues, Preset } from "@/lib/api";

export interface PresetsProps {
  presets: Preset[];
  /** The engine's own defaults — what a preset's params are layered over. */
  defaults: ParamValues;
  values: ParamValues;
  onPick: (preset: Preset) => void;
  disabled?: boolean;
}

/** Which preset the current values match, if any. */
export function activePreset(presets: Preset[], values: ParamValues, defaults: ParamValues): string | null {
  for (const p of presets) {
    const want = { ...defaults, ...p.params };
    if (Object.keys(want).every((k) => String(want[k]) === String(values[k]))) return p.id;
  }
  return null;
}

export function Presets({ presets, defaults, values, onPick, disabled }: PresetsProps) {
  const active = activePreset(presets, values, defaults);
  return (
    <section aria-label="Presets" className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Preset</h3>
      <div className="grid grid-cols-2 gap-2">
        {presets.map((p) => {
          const on = p.id === active;
          return (
            <button
              key={p.id}
              type="button"
              disabled={disabled}
              onClick={() => onPick(p)}
              aria-pressed={on}
              title={`${p.description}\n${p.detail}`}
              className={`group flex items-center gap-2 rounded-md p-1.5 text-left transition-colors disabled:opacity-50
                ${on ? "bg-accent ring-1 ring-primary/20" : "hover:bg-accent/60"}`}
            >
              <img src={`/presets/${p.sample}`} alt="" className="checker h-9 w-9 shrink-0 rounded-sm object-contain" />
              <span className="min-w-0">
                <span className="flex items-center gap-1">
                  {on && <Check className="h-3 w-3 shrink-0 text-brand" aria-hidden="true" />}
                  <span className="truncate text-xs font-medium">{p.label}</span>
                </span>
                <span className="tabular block truncate text-[11px] text-muted-foreground">{p.detail}</span>
              </span>
            </button>
          );
        })}
      </div>
      {active && <p className="text-xs text-muted-foreground">{presets.find((p) => p.id === active)?.description}</p>}
    </section>
  );
}
