import { RotateCcw } from "lucide-react";

import type { ParamValues } from "@/lib/api";
import { groupSpecs, isDefault, type ParamSpec } from "@/lib/schema";
import { ParamControl } from "./ParamControl";

export interface ParamPanelProps {
  engine: string;
  specs: ParamSpec[];
  values: ParamValues;
  onChange: (name: string, value: unknown) => void;
  onReset: () => void;
  disabled?: boolean;
  invalidField?: string | null;
}

export function ParamPanel({ engine, specs, values, onChange, onReset, disabled, invalidField }: ParamPanelProps) {
  const groups = groupSpecs(specs);
  const pristine = isDefault(specs, values);
  return (
    <div className="space-y-6" data-engine={engine}>
      {groups.map((g) => (
        <section key={g.group} aria-label={g.group} className="space-y-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{g.group}</h3>
          {g.specs.map((spec) => (
            <ParamControl
              key={spec.name}
              spec={spec}
              value={values[spec.name] ?? spec.default}
              onChange={(v) => onChange(spec.name, v)}
              disabled={disabled}
              invalid={invalidField === spec.name}
            />
          ))}
        </section>
      ))}
      <button type="button" className="btn-ghost btn-sm -ml-3 text-muted-foreground" onClick={onReset} disabled={disabled || pristine}>
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        Reset to defaults
      </button>
    </div>
  );
}
