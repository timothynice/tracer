import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import type { Settings } from "@/platform/types";
import { SettingsView } from "./SettingsView";

export interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings;
  onChange: (next: Settings) => void;
}

/** Settings in a browser, where there is no second window to open. */
export function SettingsSheet({ open, onOpenChange, settings, onChange }: SettingsSheetProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30" />
        <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-16 z-50 w-[520px] max-w-[calc(100vw-32px)] -translate-x-1/2 overflow-hidden rounded-xl border bg-background shadow-elevated">
          <div className="flex h-11 items-center justify-between border-b px-4">
            <Dialog.Title className="text-[13px] font-semibold">Settings</Dialog.Title>
            <Dialog.Close className="mac-icon" aria-label="Close">
              <X className="h-4 w-4" aria-hidden="true" />
            </Dialog.Close>
          </div>
          <SettingsView settings={settings} onChange={onChange} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
