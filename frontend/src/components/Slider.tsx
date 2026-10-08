import * as RadixSlider from "@radix-ui/react-slider";
import type { ComponentProps } from "react";

interface SliderProps extends Omit<ComponentProps<typeof RadixSlider.Root>, "value" | "onValueChange" | "defaultValue"> {
  value: number;
  onValueChange: (value: number) => void;
  /** The thumb's accessible name. */
  "aria-label"?: string;
}

/** The one slider: the accent's fill on a neutral track and a white thumb, as the switch. */
export function Slider({ value, onValueChange, className = "", "aria-label": label, ...rest }: SliderProps) {
  return (
    <RadixSlider.Root value={[value]} onValueChange={([v]) => onValueChange(v)} className={`relative flex h-5 w-full touch-none select-none items-center data-[disabled]:opacity-50 ${className}`} {...rest}>
      <RadixSlider.Track className="mac-slider-track">
        <RadixSlider.Range className="mac-slider-range" />
      </RadixSlider.Track>
      <RadixSlider.Thumb aria-label={label} className="mac-slider-thumb" />
    </RadixSlider.Root>
  );
}
