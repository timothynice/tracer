import { Switch } from "./Switch";
import { useRef, type KeyboardEvent, type ReactNode } from "react";

import type { Settings } from "@/platform/types";

export interface SettingsViewProps {
  settings: Settings;
  onChange: (next: Settings) => void;
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="px-1 text-[11px] font-semibold text-muted-foreground">{title}</h3>
      <div className="divide-y rounded-xl border bg-card">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-10 items-center justify-between gap-4 px-3 py-2">
      <div className="min-w-0">
        <p className="text-[13px]">{label}</p>
        {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  // one tab stop (the checked option); the arrow keys move between the options, wrapping, and choose as they go
  const onKeyDown = (e: KeyboardEvent, at: number) => {
    if (e.metaKey || e.altKey || e.ctrlKey) return; // a shortcut's, not the group's
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (at + step + options.length) % options.length;
    onChange(options[next][0]);
    buttons.current[next]?.focus();
  };
  const checked = options.findIndex(([v]) => v === value);
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex shrink-0 rounded-md bg-muted p-0.5">
      {options.map(([v, text], i) => (
        <button
          key={v}
          ref={(el) => (buttons.current[i] = el)}
          type="button"
          role="radio"
          aria-checked={value === v}
          tabIndex={i === Math.max(0, checked) ? 0 : -1}
          onClick={() => onChange(v)}
          onKeyDown={(e) => onKeyDown(e, i)}
          className="h-6 rounded-[5px] px-2.5 text-[12px] font-medium text-muted-foreground aria-checked:bg-background aria-checked:text-foreground aria-checked:shadow-sm"
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return <Switch aria-label={label} checked={checked} onCheckedChange={onChange} />;
}

/** The settings, as grouped rows in the manner of System Settings. */
export function SettingsView({ settings, onChange }: SettingsViewProps) {
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => onChange({ ...settings, [key]: value });
  return (
    <div className="space-y-5 p-5">
      <Group title="General">
        <Row label="Appearance">
          <Choice label="Appearance" value={settings.appearance} options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} onChange={(v) => set("appearance", v)} />
        </Row>
      </Group>
      <Group title="Export">
        <Row label="Save exports">
          <Choice label="Save exports" value={settings.exportTo} options={[["ask", "Ask each time"], ["beside", "Next to the original"]]} onChange={(v) => set("exportTo", v)} />
        </Row>
        <Row label="Show in Finder after export">
          <Toggle label="Show in Finder after export" checked={settings.revealAfterExport} onChange={(v) => set("revealAfterExport", v)} />
        </Row>
      </Group>
      <Group title="Tracing">
        <Row label="Trace new images straight away" hint="With Auto, as soon as they are opened">
          <Toggle label="Trace new images straight away" checked={settings.traceOnOpen} onChange={(v) => set("traceOnOpen", v)} />
        </Row>
        <Row label="Update automatically when tracing is quick" hint="Traces again as you change a setting, for images that trace in under 2 seconds">
          <Toggle label="Update automatically when tracing is quick" checked={settings.liveUpdate} onChange={(v) => set("liveUpdate", v)} />
        </Row>
      </Group>
    </div>
  );
}
