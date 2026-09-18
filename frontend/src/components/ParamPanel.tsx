import { RotateCcw } from "lucide-react";

import type { ParamValues } from "@/lib/api";
import { groupSpecs, isDefault, type ParamSpec } from "@/lib/schema";
import { Drawer } from "./Drawer";
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
    <div data-engine={engine}>
      {groups.map((g) => {
        const changed = g.specs.filter((spec) => (values[spec.name] ?? spec.default) !== spec.default).length;
        const holdsInvalid = invalidField !== null && g.specs.some((spec) => spec.name === invalidField);
        return (
          <Drawer
            key={g.group}
            title={g.group}
            defaultOpen={holdsInvalid}
            badge={changed > 0 ? <span className="tabular rounded-full bg-secondary px-1.5 text-[10px] font-medium text-secondary-foreground">{changed}</span> : undefined}
          >
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
          </Drawer>
        );
      })}
      <button type="button" className="btn-ghost btn-sm -ml-3 mt-4 text-muted-foreground" onClick={onReset} disabled={disabled || pristine}>
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        Reset to defaults
      </button>
    </div>
  );
}
