import { Check } from "lucide-react";
import { useMemo, type ReactNode } from "react";

import type { AutoCandidate, AutoResult, ParamValues, Preset } from "@/lib/api";

export interface PresetsProps {
  presets: Preset[];
  /** The engine's own defaults — what a preset's params are layered over. */
  defaults: ParamValues;
  values: ParamValues;
  onPick: (preset: Preset) => void;
  disabled?: boolean;
  /** The row that is on. Left out, it is whichever fixed preset the values match. */
  active?: string | null;
  /** This image's Auto run, once there has been one: every candidate's own result. */
  auto?: AutoResult | null;
  /** Auto is tracing this image right now. */
  autoRunning?: boolean;
}

/** Which fixed preset the current values match, if any. Auto has no values of its own, so it never matches. */
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

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function Thumb({ preset, candidate }: { preset: Preset; candidate?: AutoCandidate }) {
  // After an Auto run each candidate shows this image traced its way, not a stock sample.
  const own = candidate?.svg ?? null;
  const src = useMemo(() => (own ? svgUrl(own) : `/presets/${preset.sample}`), [own, preset.sample]);
  return <img src={src} alt="" data-own={own ? "" : undefined} className="checker h-8 w-8 shrink-0 rounded-sm object-contain" />;
}

/** This image's own result for one candidate, in one line: fidelity, size, and whether it came out clean. */
function Outcome({ candidate }: { candidate: AutoCandidate }) {
  if (candidate.error) return <span className="text-destructive">Failed: {candidate.error.message}</span>;
  const s = candidate.scores;
  if (!s) return <span>{candidate.stats?.paths ?? "?"} shapes · not scored</span>;
  const [first, ...rest] = s.issues;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="tabular shrink-0">ΔE {s.delta_e.toFixed(2)} · {s.shapes} shapes</span>
      <span className="inline-flex min-w-0 items-center gap-1" data-clean={s.clean}>
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${s.clean ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
        <span className="truncate">{s.clean ? "clean" : `${first ?? "issues"}${rest.length ? ` +${rest.length}` : ""}`}</span>
      </span>
    </span>
  );
}

export function Presets({ presets, defaults, values, onPick, disabled, active: activeProp, auto, autoRunning }: PresetsProps) {
  const active = activeProp === undefined ? activePreset(presets, values, defaults) : activeProp;
  const hasAuto = presets.some((p) => p.kind === "auto");
  const candidates = presets.filter((p) => p.auto_candidate);
  const byId = new Map((auto?.candidates ?? []).map((c) => [c.preset, c]));
  const chosen = auto?.pick ?? null;
  const shown = presets.find((p) => p.id === active);

  const row = (p: Preset) => {
    const on = p.id === active;
    const cand = byId.get(p.id);
    const isAuto = p.kind === "auto";
    const issues = cand?.scores && !cand.scores.clean ? `\n\nOn this image: ${cand.scores.issues.join(", ")}` : "";
    let sub: ReactNode = null;
    if (isAuto) {
      const choice = auto ? autoChoice(auto, presets) : "";
      sub = autoRunning
        ? `Trying ${candidates.length} presets on your image…`
        : auto
          ? <span aria-label={`Auto ${choice}`}>{choice.charAt(0).toUpperCase() + choice.slice(1)}</span>
          : `Tries ${candidates.length} presets and keeps the cleanest`;
    } else if (cand) {
      sub = <Outcome candidate={cand} />;
    }
    return (
      <button
        key={p.id}
        type="button"
        disabled={disabled}
        onClick={() => onPick(p)}
        aria-pressed={on}
        data-preset={p.id}
        title={`${p.description}\n${p.detail}${issues}${on && !isAuto ? "\n\nClick again to restore these settings" : ""}`}
        className={`group flex w-full items-center gap-2 rounded-md p-1.5 text-left transition-colors disabled:opacity-50
          ${on ? "bg-accent ring-1 ring-primary/20" : "hover:bg-accent/60"}`}
      >
        <Thumb preset={p} candidate={cand} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            {on && <Check className="h-3 w-3 shrink-0 text-brand" aria-hidden="true" />}
            <span className={`truncate text-xs ${on ? "font-semibold" : "font-medium"}`}>{p.label}</span>
            {chosen === p.id && (
              <span
                className="inline-flex shrink-0 items-center gap-1 rounded-sm bg-secondary px-1 text-[10px] font-medium leading-4 text-secondary-foreground"
                title="Auto's choice for this image"
              >
                <span className="dot-brand" aria-hidden="true" />
                Auto's pick
              </span>
            )}
          </span>
          {sub && (
            <span
              aria-live={isAuto ? "polite" : undefined}
              className={`mt-0.5 block text-[11px] leading-tight text-muted-foreground ${isAuto ? "" : "truncate"}`}
            >
              {sub}
            </span>
          )}
        </span>
      </button>
    );
  };

  const leading = presets.filter((p) => !hasAuto || p.kind === "auto" || p.auto_candidate);
  const trailing = hasAuto ? presets.filter((p) => p.kind !== "auto" && !p.auto_candidate) : [];

  return (
    <section aria-label="Presets" className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Preset</h3>
      <div className="space-y-1">{leading.map(row)}</div>
      {trailing.length > 0 && (
        <div className="space-y-1">
          <h4 className="px-1.5 text-[11px] font-medium text-muted-foreground">Style & format — never picked by Auto</h4>
          {trailing.map(row)}
        </div>
      )}
      {shown && (
        <div className="space-y-1">
          <p className="text-xs leading-snug text-muted-foreground">{shown.description}</p>
          <p className="tabular text-[11px] text-muted-foreground/80">{shown.detail}</p>
        </div>
      )}
    </section>
  );
}
