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
      <div className="space-y-1">
        {presets.map((p) => {
          const on = p.id === active;
          return (
            <button
              key={p.id}
              type="button"
              disabled={disabled}
              onClick={() => onPick(p)}
              aria-pressed={on}
              title={on ? `${p.description}\n${p.detail}\n\nClick again to restore these settings` : `${p.description}\n${p.detail}`}
              className={`group flex items-center gap-2 rounded-md p-1.5 text-left transition-colors disabled:opacity-50
                ${on ? "bg-accent ring-1 ring-primary/20" : "hover:bg-accent/60"}`}
            >
              <img src={`/presets/${p.sample}`} alt="" className="checker h-8 w-8 shrink-0 rounded-sm object-contain" />
              {on && <Check className="h-3 w-3 shrink-0 text-brand" aria-hidden="true" />}
              <span className="min-w-0 flex-1 truncate text-xs font-medium">{p.label}</span>
            </button>
          );
        })}
      </div>
      {active && (
        <div className="space-y-1">
          <p className="text-xs leading-snug text-muted-foreground">{presets.find((p) => p.id === active)?.description}</p>
          <p className="tabular text-[11px] text-muted-foreground/80">{presets.find((p) => p.id === active)?.detail}</p>
        </div>
      )}
    </section>
  );
}
