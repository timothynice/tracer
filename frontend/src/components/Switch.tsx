import * as RadixSwitch from "@radix-ui/react-switch";

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  id?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  disabled?: boolean;
}

/** The one switch of the app: the accent when on, a neutral track when off (styles in `.mac-switch`). */
export function Switch(props: SwitchProps) {
  return (
    <RadixSwitch.Root className="mac-switch" {...props}>
      <RadixSwitch.Thumb className="mac-switch-thumb" />
    </RadixSwitch.Root>
  );
}
