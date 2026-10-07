import { ChevronRight, Sparkles, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { AutoResult, ParamValues, Preset } from "@/lib/api";

/** Auto's icon; the styles are rows without one. */
export const PRESET_ICONS: Record<string, LucideIcon> = { auto: Sparkles };

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

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Auto as the hero, and every style one row under "Choose a style": one radiogroup, the arrows walking all of it. */
export function PresetCards({ presets, defaults, values, active, auto, autoRunning, onPick, disabled }: PresetCardsProps) {
  const on = active ?? activePreset(presets, values, defaults);
  const autoPreset = presets.find((p) => p.kind === "auto") ?? null;
  const styles = presets.filter((p) => p.kind !== "auto");
  const candidates = styles.filter((p) => p.auto_candidate);
  const trailing = styles.filter((p) => !p.auto_candidate);
  const ordered = autoPreset ? [autoPreset, ...candidates, ...trailing] : [...candidates, ...trailing];
  const styleOn = on !== null && on !== "auto";
  const [open, setOpen] = useState(styleOn || !autoPreset);
  useEffect(() => {
    if (styleOn) setOpen(true); // a chosen style is never hidden
  }, [styleOn]);
  const rows = useRef(new Map<string, HTMLElement>());
  // a pick by the arrow keys focuses its row once it is rendered (the disclosure may be opening for it)
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const el = pendingFocus.current ? rows.current.get(pendingFocus.current) : undefined;
    if (el) {
      el.focus();
      pendingFocus.current = null;
    }
  });
  const byId = new Map((auto?.candidates ?? []).map((c) => [c.preset, c]));

  const move = (e: KeyboardEvent, at: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (step) {
      e.preventDefault();
      const next = ordered[Math.max(0, Math.min(ordered.length - 1, at + step))];
      if (next) {
        pendingFocus.current = next.id;
        onPick(next);
      }
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      onPick(ordered[at]);
    }
  };
  const bind = (p: Preset, at: number) => ({
    ref: (el: HTMLElement | null) => {
      if (el) rows.current.set(p.id, el);
      else rows.current.delete(p.id);
    },
    role: "radio" as const,
    "aria-checked": p.id === on,
    "aria-disabled": disabled || undefined,
    "aria-label": p.label,
    tabIndex: p.id === on || (on === null && at === 0) ? 0 : -1,
    title: `${p.description}\n${p.detail}`,
    onClick: () => !disabled && onPick(p),
    onKeyDown: (e: KeyboardEvent) => !disabled && move(e, at),
  });

  const n = candidates.length;
  let autoLine = `Tries ${n} styles and keeps the cleanest faithful one.`;
  let autoLabel: string | undefined;
  if (autoRunning) autoLine = `Trying ${n} styles…`;
  else if (auto) {
    const choice = autoChoice(auto, presets);
    autoLine = capital(choice);
    autoLabel = `Auto ${choice}`;
  }

  const row = (p: Preset, at: number) => {
    const cand = byId.get(p.id);
    let verdict = null;
    if (cand?.scores)
      verdict = (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cand.scores.clean ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
          <span className="truncate">{cand.scores.clean ? "Clean" : cand.scores.issues.join(", ")}</span>
        </span>
      );
    else if (cand?.error) verdict = <span className="truncate text-[11px] text-destructive">{cand.error.message}</span>;
    return (
      <div key={p.id} {...bind(p, at)} className="mac-choice flex h-9 items-center gap-2.5 px-2.5 py-0">
        <span className="mac-radio" aria-hidden="true" />
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {auto?.pick === p.id && (
            <>
              <span className="dot-brand shrink-0" aria-hidden="true" />
              <span className="sr-only">Auto's pick</span>
            </>
          )}
          <span className="truncate text-[13px]">{p.label}</span>
        </span>
        {verdict}
      </div>
    );
  };
  const first = autoPreset ? 1 : 0;

  return (
    <section className="space-y-1.5">
      <div role="radiogroup" aria-label="Preset" className="space-y-1.5">
        {autoPreset && (
          <div {...bind(autoPreset, 0)} className="mac-choice flex items-center gap-3 p-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold">{autoPreset.label}</span>
              <span className="mt-0.5 block text-[12px] leading-snug text-muted-foreground" aria-live="polite" aria-label={autoLabel}>
                {autoLine}
              </span>
            </span>
            <span className="mac-radio" aria-hidden="true" />
          </div>
        )}
        {autoPreset && (
          <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="mac-ghost h-8 w-full px-1.5 text-foreground">
            <ChevronRight className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
            Choose a style
          </button>
        )}
        {open && (
          <div className="space-y-0.5">
            {candidates.map((p, i) => row(p, first + i))}
            {trailing.length > 0 && candidates.length > 0 && <div aria-hidden="true" className="mx-2.5 my-1 h-px bg-border" />}
            {trailing.map((p, i) => row(p, first + candidates.length + i))}
          </div>
        )}
      </div>
    </section>
  );
}
