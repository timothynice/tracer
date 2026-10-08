import * as Select from "@radix-ui/react-select";
import { Slider } from "./Slider";
import { Switch } from "./Switch";
import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { sanitize, type ParamSpec } from "@/lib/schema";

export interface ParamControlProps {
  spec: ParamSpec;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
  /** Highlight as invalid (e.g. a 422 pointed at this field). */
  invalid?: boolean;
}

function decimals(step: number): number {
  const s = String(step);
  return s.includes(".") ? s.split(".")[1].length : 0;
}

function trim(s: string): string {
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}

export function ParamControl({ spec, value, onChange, disabled, invalid }: ParamControlProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const descId = spec.description ? `${id}-desc` : undefined;

  const head = (
    <div className="flex items-baseline justify-between gap-3">
      <label id={labelId} htmlFor={id} className="text-sm font-medium">
        {spec.label}
      </label>
      {spec.control === "slider" && (
        <NumberField id={id} spec={spec} value={Number(value)} onChange={onChange} disabled={disabled} invalid={invalid} />
      )}
    </div>
  );

  return (
    <div className="space-y-2" data-param={spec.name}>
      {spec.control === "toggle" ? (
        <div className="flex items-center justify-between gap-3">
          <label id={labelId} htmlFor={id} className="text-sm font-medium">
            {spec.label}
          </label>
          <Switch id={id} checked={Boolean(value)} onCheckedChange={onChange} disabled={disabled} aria-describedby={descId} />
        </div>
      ) : (
        head
      )}

      {spec.control === "slider" && (
        <Slider aria-labelledby={labelId} aria-describedby={descId} aria-label={spec.label} value={Number(value)} min={spec.min ?? 0} max={spec.max ?? 100} step={spec.step} disabled={disabled} onValueChange={onChange} />
      )}

      {spec.control === "select" && (
        <Select.Root value={String(value)} onValueChange={onChange} disabled={disabled}>
          <Select.Trigger
            id={id}
            aria-labelledby={labelId}
            aria-describedby={descId}
            className={`mac-button h-8 w-full justify-between font-normal capitalize ${invalid ? "ring-2 ring-destructive" : ""}`}
          >
            <Select.Value />
            <Select.Icon>
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            </Select.Icon>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content position="popper" sideOffset={4} className="mac-menu min-w-[var(--radix-select-trigger-width)]">
              <Select.Viewport className="p-1">
                {spec.options?.map((opt) => (
                  <Select.Item
                    key={opt}
                    value={opt}
                    className="mac-menu-item relative h-7 pl-7 capitalize"
                  >
                    <Select.ItemIndicator className="absolute left-1.5 inline-flex items-center">
                      <Check className="h-4 w-4" />
                    </Select.ItemIndicator>
                    <Select.ItemText>{opt}</Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Viewport>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
      )}

      {spec.description && (
        <p id={descId} className="text-xs text-muted-foreground">
          {spec.description}
        </p>
      )}
    </div>
  );
}

function NumberField({
  id,
  spec,
  value,
  onChange,
  disabled,
  invalid,
}: {
  id: string;
  spec: ParamSpec;
  value: number;
  onChange: (v: unknown) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  // Trailing zeros only go after a decimal point: stripping them unconditionally
  // turns 70 into "7" and 100 into "1".
  const formatted = Number.isFinite(value) ? trim(value.toFixed(decimals(spec.step))) : "";
  const [text, setText] = useState(formatted);
  useEffect(() => setText(formatted), [formatted]);
  const show = () => setText(formatted);
  // Only a changed value is a change: leaving a field as it was must not move the settings (it would drop Auto), and
  // an emptied field (Number("") is 0) or text that is not a number goes back to what it was.
  const commit = () => {
    const n = text.trim() === "" ? NaN : Number(text);
    if (!Number.isFinite(n)) return show();
    const next = sanitize(spec, n) as number;
    if (next === value) return show();
    onChange(next);
  };

  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <input
        id={id}
        type="number"
        inputMode="decimal"
        value={text}
        min={spec.min}
        max={spec.max}
        step={spec.step}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        className={`tabular h-7 w-16 rounded-md border bg-background/70 pl-2 pr-2.5 text-right text-xs text-foreground ${invalid ? "border-destructive" : ""}`}
      />
      {spec.unit && <span aria-hidden="true">{spec.unit}</span>}
    </span>
  );
}
