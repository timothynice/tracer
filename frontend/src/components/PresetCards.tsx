import { ChevronRight, Circle, Layers, LayoutGrid, Mountain, Scissors, Shapes, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import type { AutoResult, ParamValues, Preset } from "@/lib/api";

/** An icon per preset id; a preset added later gets the shapes. */
export const PRESET_ICONS: Record<string, LucideIcon> = { auto: Sparkles, balanced: LayoutGrid, logo: Mountain, detailed: SlidersHorizontal, dense: Circle, flat: Layers, cutfile: Scissors };

export function firstSentence(text: string): string {
  const m = /^(.+?[.!?])(\s|$)/.exec(text.trim());
  return m ? m[1] : text.trim();
}

/** Which fixed preset the values match, if any. Auto has no values of its own, so it never matches. */
export function activePreset(presets: Preset[], values: ParamValues, defaults: ParamValues): string | null {
  for (const p of presets) {
    if (p.kind === "auto") continue;
    const want = { ...defaults, ...p.params };
    if (Object.keys(want).every((k) => String(want[k]) === String(values[k]))) return p.id;
  }
  return null;
}

/** What one Auto run chose, as "chose Logo & icon — the cleanest at the same fidelity". */
export function autoChoice(auto: AutoResult, presets: Preset[]): string {
  if (!auto.pick) return `couldn't choose — ${auto.reason}`;
  const label = presets.find((p) => p.id === auto.pick)?.label ?? auto.candidates.find((c) => c.preset === auto.pick)?.label ?? auto.pick;
  return `chose ${label} — ${auto.reason}`;
}

export interface PresetCardsProps {
  presets: Preset[];
  defaults: ParamValues;
  values: ParamValues;
  /** "auto", a preset's id, or null when the values were set by hand (then whichever preset they match, if any). */
  active: string | null;
  auto: AutoResult | null;
  autoRunning: boolean;
  onPick: (preset: Preset) => void;
  disabled?: boolean;
}

export function PresetCards({ presets, defaults, values, active, auto, autoRunning, onPick, disabled }: PresetCardsProps) {
  const [more, setMore] = useState(false);
  const on = active ?? activePreset(presets, values, defaults);
  const hasAuto = presets.some((p) => p.kind === "auto");
  const leading = presets.filter((p) => !hasAuto || p.kind === "auto" || p.auto_candidate);
  const trailing = hasAuto ? presets.filter((p) => p.kind !== "auto" && !p.auto_candidate) : [];
  const shown = more || trailing.some((p) => p.id === on) ? [...leading, ...trailing] : leading;
  const candidates = presets.filter((p) => p.auto_candidate).length;
  const byId = new Map((auto?.candidates ?? []).map((c) => [c.preset, c]));

  const move = (e: KeyboardEvent, at: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (step) {
      e.preventDefault();
      const next = shown[Math.max(0, Math.min(shown.length - 1, at + step))];
      if (next) onPick(next);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      onPick(shown[at]);
    }
  };

  const card = (p: Preset, at: number) => {
    const checked = p.id === on;
    const Icon = PRESET_ICONS[p.id] ?? Shapes;
    const cand = byId.get(p.id);
    let sub = <span className="line-clamp-2">{firstSentence(p.description)}</span>;
    if (p.kind === "auto" && autoRunning) sub = <span>Trying {candidates} presets on your image…</span>;
    else if (p.kind === "auto" && auto) {
      const choice = autoChoice(auto, presets);
      sub = <span aria-label={`Auto ${choice}`}>{choice.charAt(0).toUpperCase() + choice.slice(1)}</span>;
    } else if (cand?.scores) {
      sub = (
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cand.scores.clean ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
          <span className="truncate">{cand.scores.clean ? "Clean" : cand.scores.issues.join(", ")}</span>
        </span>
      );
    } else if (cand?.error) sub = <span className="text-destructive">{cand.error.message}</span>;
    return (
      <div
        key={p.id}
        role="radio"
        aria-checked={checked}
        aria-disabled={disabled || undefined}
        tabIndex={checked || (on === null && at === 0) ? 0 : -1}
        title={`${p.description}\n${p.detail}`}
        onClick={() => !disabled && onPick(p)}
        onKeyDown={(e) => !disabled && move(e, at)}
        className="mac-choice flex items-center gap-3"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold">{p.label}</span>
            {auto?.pick === p.id && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded bg-secondary px-1 text-[10px] font-medium leading-4">
                <span className="dot-brand" aria-hidden="true" />
                Auto's pick
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground" aria-live={p.kind === "auto" ? "polite" : undefined}>
            {sub}
          </span>
        </span>
        <span className="mac-radio" aria-hidden="true" />
      </div>
    );
  };

  return (
    <section className="space-y-1.5">
      <div role="radiogroup" aria-label="Preset" className="space-y-1.5">
        {shown.map(card)}
      </div>
      {trailing.length > 0 && !trailing.some((p) => p.id === on) && (
        <button type="button" aria-expanded={more} onClick={() => setMore((v) => !v)} className="mac-ghost h-7 px-1.5 text-[12px]">
          <ChevronRight className={`h-3.5 w-3.5 transition-transform ${more ? "rotate-90" : ""}`} aria-hidden="true" />
          More Styles
        </button>
      )}
    </section>
  );
}
