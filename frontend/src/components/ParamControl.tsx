import * as Select from "@radix-ui/react-select";
import * as Slider from "@radix-ui/react-slider";
import * as Switch from "@radix-ui/react-switch";
import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type { ParamSpec } from "@/lib/schema";

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
          <Switch.Root
            id={id}
            checked={Boolean(value)}
            onCheckedChange={onChange}
            disabled={disabled}
            aria-describedby={descId}
            className="relative h-6 w-11 shrink-0 rounded-full bg-muted transition-colors data-[state=checked]:bg-primary disabled:opacity-50"
          >
            <Switch.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-background shadow-sm transition-transform data-[state=checked]:translate-x-[22px] data-[state=checked]:bg-primary-foreground" />
          </Switch.Root>
        </div>
      ) : (
        head
      )}

      {spec.control === "slider" && (
        <Slider.Root
          aria-labelledby={labelId}
          aria-describedby={descId}
          value={[Number(value)]}
          min={spec.min ?? 0}
          max={spec.max ?? 100}
          step={spec.step}
          disabled={disabled}
          onValueChange={([v]) => onChange(v)}
          className="relative flex h-5 w-full touch-none select-none items-center"
        >
          <Slider.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-muted">
            <Slider.Range className="absolute h-full bg-primary" />
          </Slider.Track>
          <Slider.Thumb
            aria-label={spec.label}
            className="block h-4 w-4 rounded-full border-2 border-primary bg-background shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none"
          />
        </Slider.Root>
      )}

      {spec.control === "select" && (
        <Select.Root value={String(value)} onValueChange={onChange} disabled={disabled}>
          <Select.Trigger
            id={id}
            aria-labelledby={labelId}
            aria-describedby={descId}
            className={`input flex items-center justify-between gap-2 text-left capitalize ${invalid ? "ring-2 ring-destructive" : ""}`}
          >
            <Select.Value />
            <Select.Icon>
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            </Select.Icon>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content position="popper" sideOffset={4} className="z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-md">
              <Select.Viewport className="p-1">
                {spec.options?.map((opt) => (
                  <Select.Item
                    key={opt}
                    value={opt}
                    className="relative flex h-9 cursor-default select-none items-center rounded-sm pl-8 pr-3 text-sm capitalize outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground"
                  >
                    <Select.ItemIndicator className="absolute left-2 inline-flex items-center">
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
  const [text, setText] = useState(String(value));
  // Trailing zeros only go after a decimal point: stripping them unconditionally
  // turns 70 into "7" and 100 into "1".
  useEffect(() => setText(Number.isFinite(value) ? trim(value.toFixed(decimals(spec.step))) : ""), [value, spec.step]);

  const commit = () => {
    const n = Number(text);
    if (Number.isFinite(n)) onChange(n);
    else setText(String(value));
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
        className={`tabular h-7 w-16 rounded-md border border-input bg-background px-2 text-right text-xs text-foreground focus-visible:ring-2 focus-visible:ring-ring ${invalid ? "border-destructive" : ""}`}
      />
      {spec.unit && <span aria-hidden="true">{spec.unit}</span>}
    </span>
  );
}
