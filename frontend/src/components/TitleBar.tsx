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
      {/* the mark's trace is pale yellow on nothing: on the light window it needs the icon's slate tile behind it */}
      <span aria-hidden="true" className="flex h-7 w-7 shrink-0 rounded-[8px] bg-[#3F4D60]">
        <img src="/brand/studi0trace-mark.svg" alt="" draggable={false} className="h-7 w-7" />
      </span>
      <h1 className="min-w-0 font-brand text-[15px] font-semibold tracking-tight">Studi0Trace</h1>
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
