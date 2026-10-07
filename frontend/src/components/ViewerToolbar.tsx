import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tabs from "@radix-ui/react-tabs";
import { ChevronDown, CircleDot, Columns2, Hand, Layers, Layers2, Maximize, PenTool, Search, Spline, SplitSquareHorizontal } from "lucide-react";

import { formatPercent } from "@/lib/format";
import type { ViewMode } from "@/platform/types";

export type Tool = "pan" | "zoom";
export const ZOOM_LEVELS = [0.5, 1, 2, 4];

const MODES: { value: ViewMode; label: string; icon: typeof Columns2 }[] = [
  { value: "split", label: "Split", icon: SplitSquareHorizontal },
  { value: "side", label: "Side by side", icon: Columns2 },
  { value: "overlay", label: "Overlay", icon: Layers2 },
  { value: "vector", label: "Vector", icon: Spline },
];

export interface ViewerToolbarProps {
  tool: Tool;
  onTool: (tool: Tool) => void;
  scale: number;
  onZoomTo: (scale: number | "fit") => void;
  mode: ViewMode;
  onMode: (mode: ViewMode) => void;
  overlay: number;
  onOverlay: (opacity: number) => void;
  hasVector: boolean;
  /** What the right-hand view is: the vector, or (comparing two rasters) the AI redraw. */
  rightLabel?: string;
  display: { points: boolean; outlines: boolean };
  onDisplayChange: (patch: { points?: boolean; outlines?: boolean }) => void;
  layersOpen: boolean;
  onToggleLayers: () => void;
}

const group = "flex items-center gap-0.5";
const divider = <span aria-hidden="true" className="mx-1 h-4 w-px bg-border" />;

/** The floating toolbar at the foot of the viewer. */
export function ViewerToolbar(p: ViewerToolbarProps) {
  const right = p.rightLabel ?? "Vector";
  return (
    <div data-overlay-ui role="toolbar" aria-label="View" className="absolute bottom-3 left-1/2 z-30 flex -translate-x-1/2 items-center rounded-xl border bg-popover/90 p-1 shadow-elevated backdrop-blur-xl">
      <div className={group}>
        <button type="button" className="mac-icon" aria-label="Hand tool" aria-pressed={p.tool === "pan"} title="Hand: drag to pan (or hold Space)" onClick={() => p.onTool("pan")}>
          <Hand className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Zoom tool" aria-pressed={p.tool === "zoom"} title="Zoom: click to zoom in, Option-click to zoom out" onClick={() => p.onTool("zoom")}>
          <Search className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      {divider}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" aria-label="Zoom level" className="tabular inline-flex h-7 min-w-[4.5rem] items-center justify-center gap-1 rounded-md px-2 text-[12px] font-medium hover:bg-accent">
            {formatPercent(p.scale)}
            <ChevronDown className="h-3 w-3 opacity-60" aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content side="top" sideOffset={6} className="mac-menu">
            <DropdownMenu.Item className="mac-menu-item" onSelect={() => p.onZoomTo("fit")}>
              Zoom to Fit
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="mac-menu-separator" />
            {ZOOM_LEVELS.map((z) => (
              <DropdownMenu.Item key={z} className="mac-menu-item" onSelect={() => p.onZoomTo(z)}>
                {formatPercent(z)}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <button type="button" className="mac-icon" aria-label="Fit to window" title="Zoom to Fit (⌘9)" onClick={() => p.onZoomTo("fit")}>
        <Maximize className="h-4 w-4" aria-hidden="true" />
      </button>
      {divider}
      <Tabs.Root value={p.mode} onValueChange={(v) => p.onMode(v as ViewMode)}>
        <Tabs.List aria-label="Compare" className="inline-flex h-7 items-center rounded-md bg-muted p-0.5">
          {MODES.map((m) => {
            const label = m.value === "vector" ? right : m.label;
            return (
              <Tabs.Trigger
                key={m.value}
                value={m.value}
                aria-label={label}
                title={label}
                className="inline-flex h-6 w-7 items-center justify-center rounded-[5px] text-muted-foreground transition-colors data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
              >
                <m.icon className="h-3.5 w-3.5" aria-hidden="true" />
              </Tabs.Trigger>
            );
          })}
        </Tabs.List>
      </Tabs.Root>
      {p.mode === "overlay" && (
        <input type="range" min={0} max={1} step={0.05} value={p.overlay} onChange={(e) => p.onOverlay(Number(e.target.value))} aria-label={`${right} opacity`} className="ml-2 w-20" style={{ accentColor: "var(--accent-mac)" }} />
      )}
      {p.hasVector && (
        <>
          {divider}
          <div className={group}>
            <button type="button" className="mac-icon" aria-label="Show anchor points" aria-pressed={p.display.points} title="Anchor points" onClick={() => p.onDisplayChange({ points: !p.display.points })}>
              <CircleDot className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="mac-icon" aria-label="Show outlines" aria-pressed={p.display.outlines} title="Outlines" onClick={() => p.onDisplayChange({ outlines: !p.display.outlines })}>
              <PenTool className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="mac-icon" aria-label="Layers" aria-pressed={p.layersOpen} title="Layers" onClick={p.onToggleLayers}>
              <Layers className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
