import { Switch } from "./Switch";
import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { keyErrorText } from "@/lib/redraw";
import type { Settings } from "@/platform/types";

/** The OpenAI key's row: whether one is stored, and the two ways to change that (the key never comes back). */
export interface RedrawKeyControls {
  stored: boolean | null;
  onSave: (key: string) => Promise<void>;
  onRemove: () => Promise<void>;
}

export interface SettingsViewProps {
  settings: Settings;
  onChange: (next: Settings) => void;
  /** The AI redraw group, in the Mac app only. */
  redraw?: RedrawKeyControls;
}

function KeyRow({ controls }: { controls: RedrawKeyControls }) {
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const status = controls.stored === null ? "…" : controls.stored ? "Stored" : "Not set";
  const close = () => {
    setEditing(false);
    setKey("");
    setError(null);
  };
  const save = async () => {
    try {
      await controls.onSave(key.trim());
      close();
      setError(null);
    } catch (err) {
      setError(keyErrorText(err, "save"));
    }
  };
  return (
    <div className="space-y-2 px-3 py-2">
      <div className="flex min-h-6 items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[13px]">OpenAI API key</p>
          <p className="text-[11px] text-muted-foreground">
            <span>{status}</span> · in your Mac's Keychain
          </p>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <button type="button" className="mac-button h-7" onClick={() => setEditing(true)}>
            {controls.stored ? "Replace" : "Add"}
          </button>
          {controls.stored && (
            <button type="button" className="mac-button h-7" onClick={() => void controls.onRemove().catch((err: unknown) => setError(keyErrorText(err, "remove")))}>
              Remove
            </button>
          )}
        </div>
      </div>
      {editing && (
        <form
          className="flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <input
            aria-label="New OpenAI API key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="h-7 min-w-0 flex-1 rounded-md border bg-background/70 px-2 text-[12px]"
          />
          <button type="submit" className="mac-button h-7" disabled={!key.trim()}>
            Save
          </button>
          <button type="button" className="mac-button h-7" onClick={close}>
            Cancel
          </button>
        </form>
      )}
      {error && (
        <p role="alert" className="text-[11px] text-destructive">
          {error}
        </p>
      )}
    </div>
  );
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
export function SettingsView({ settings, onChange, redraw }: SettingsViewProps) {
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => onChange({ ...settings, [key]: value });
  return (
    <div className="space-y-5 p-5">
      <Group title="General">
        <Row label="Appearance">
          <Choice label="Appearance" value={settings.appearance} options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} onChange={(v) => set("appearance", v)} />
        </Row>
        <Row label="Check for updates automatically" hint="When Studi0Trace opens">
          <Toggle label="Check for updates automatically" checked={settings.checkForUpdates} onChange={(v) => set("checkForUpdates", v)} />
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
      {redraw && (
        <Group title="AI redraw">
          <KeyRow controls={redraw} />
          <Row label="Model">
            <Choice label="Model" value={settings.redrawModel} options={[["gpt-image-2", "GPT Image 2"], ["gpt-image-1.5", "GPT Image 1.5"]]} onChange={(v) => set("redrawModel", v)} />
          </Row>
          <Row label="Quality" hint="High costs more and takes longer">
            <Choice label="Quality" value={settings.redrawQuality} options={[["medium", "Medium"], ["high", "High"]]} onChange={(v) => set("redrawQuality", v)} />
          </Row>
          <Row label="Suggest for rough images" hint="A hint in the inspector for small or pixel-doubled images">
            <Toggle label="Suggest for rough images" checked={settings.suggestRedraw} onChange={(v) => set("suggestRedraw", v)} />
          </Row>
        </Group>
      )}
    </div>
  );
}
