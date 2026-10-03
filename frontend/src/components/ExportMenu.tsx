import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronDown, Download } from "lucide-react";

export interface ExportMenuProps {
  canExport: boolean;
  anyVector: boolean;
  onExport: (kind: "svg" | "png", scale: number) => void;
  onCopy: () => void;
  onExportAll: () => void;
}

/** Export SVG, with the rest a click away: PNG at three sizes, Copy SVG, Export All. */
export function ExportMenu({ canExport, anyVector, onExport, onCopy, onExportAll }: ExportMenuProps) {
  return (
    <div className="flex">
      <button type="button" className="mac-button h-9 flex-1 rounded-r-none" disabled={!canExport} onClick={() => onExport("svg", 1)} title="Export SVG… (⌘E)">
        <Download className="h-4 w-4" aria-hidden="true" />
        Export SVG
      </button>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" aria-label="More export options" className="mac-button h-9 rounded-l-none border-l-0 px-2" disabled={!canExport && !anyVector}>
            <ChevronDown className="h-4 w-4" aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={6} className="mac-menu">
            <DropdownMenu.Item className="mac-menu-item" disabled={!canExport} onSelect={() => onExport("svg", 1)}>
              Export SVG…<span className="mac-menu-shortcut">⌘E</span>
            </DropdownMenu.Item>
            {[1, 2, 4].map((s) => (
              <DropdownMenu.Item key={s} className="mac-menu-item" disabled={!canExport} onSelect={() => onExport("png", s)}>
                Export PNG at {s}×…{s === 2 && <span className="mac-menu-shortcut">⇧⌘E</span>}
              </DropdownMenu.Item>
            ))}
            <DropdownMenu.Separator className="mac-menu-separator" />
            <DropdownMenu.Item className="mac-menu-item" disabled={!canExport} onSelect={onCopy}>
              Copy SVG<span className="mac-menu-shortcut">⇧⌘C</span>
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="mac-menu-separator" />
            <DropdownMenu.Item className="mac-menu-item" disabled={!anyVector} onSelect={onExportAll}>
              Export All…<span className="mac-menu-shortcut">⌥⌘E</span>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}
