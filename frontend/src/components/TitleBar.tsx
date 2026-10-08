import { PanelLeft, PanelRight, Settings as Gear } from "lucide-react";

export interface TitleBarProps {
  /** In the Mac app the traffic lights sit at the left of the bar. */
  native: boolean;
  sidebar: boolean;
  inspector: boolean;
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
  onSettings: () => void;
}

/** The unified title bar: the window drags by all of it but its buttons, and a double-click zooms it. */
export function TitleBar({ native, sidebar, inspector, onToggleSidebar, onToggleInspector, onSettings }: TitleBarProps) {
  return (
    <header data-tauri-drag-region="deep" className="flex h-[52px] shrink-0 items-center gap-2.5 pr-3" style={{ paddingLeft: native ? 88 : 16 }}>
      {/* the wordmark follows the appearance; the icon tile runs against it (the slate tile on the light window, the white one under `.dark`), so the tile always stands off the bar */}
      <img src="/brand/icon-slate.png" alt="" draggable={false} className="h-7 w-auto shrink-0 dark:hidden" />
      <img src="/brand/icon-white.png" alt="" draggable={false} className="hidden h-7 w-auto shrink-0 dark:block" />
      <h1 className="flex min-w-0 items-center">
        <img src="/brand/wordmark-light.png" alt="Studi0Trace" draggable={false} className="h-[15px] w-auto dark:hidden" />
        <img src="/brand/wordmark-dark.png" alt="" aria-hidden="true" draggable={false} className="hidden h-[15px] w-auto dark:block" />
      </h1>
      <div className="ml-auto flex items-center gap-0.5">
        <button type="button" className="mac-icon" aria-label="Show sidebar" aria-pressed={sidebar} title="Show Sidebar (⌃⌘S)" onClick={onToggleSidebar}>
          <PanelLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Show inspector" aria-pressed={inspector} title="Show Inspector (⌥⌘I)" onClick={onToggleInspector}>
          <PanelRight className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Settings" title="Settings (⌘,)" onClick={onSettings}>
          <Gear className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
