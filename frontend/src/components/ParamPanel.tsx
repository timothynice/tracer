import type { ParamValues } from "@/lib/api";
import { groupSpecs, type ParamSpec } from "@/lib/schema";
import { Drawer } from "./Drawer";
import { ParamControl } from "./ParamControl";

export interface ParamPanelProps {
  engine: string;
  specs: ParamSpec[];
  values: ParamValues;
  onChange: (name: string, value: unknown) => void;
  disabled?: boolean;
  invalidField?: string | null;
}

// There is no reset here on purpose: re-picking the preset you are already on
// re-applies it over the engine defaults, which is the same thing.
export function ParamPanel({ engine, specs, values, onChange, disabled, invalidField }: ParamPanelProps) {
  const groups = groupSpecs(specs);
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
    </div>
  );
}
