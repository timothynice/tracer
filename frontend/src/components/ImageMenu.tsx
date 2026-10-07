import * as ContextMenu from "@radix-ui/react-context-menu";
import type { ReactNode } from "react";

import type { ImageItem } from "@/state/library";

export interface ImageMenuProps {
  item: ImageItem;
  children: ReactNode;
  onSelect: () => void;
  onGenerate: () => void;
  onExport: () => void;
  onReveal: () => void;
  onRemove: () => void;
  /** AI redraw's items (the Mac app). */
  redraw?: { onRedraw: () => void; onShowOriginal: () => void; onRevert: () => void };
}

/** A right click on an image card: it is selected, as in Finder, and offers what applies to it. */
export function ImageMenu({ item, children, onSelect, onGenerate, onExport, onReveal, onRemove, redraw }: ImageMenuProps) {
  return (
    <ContextMenu.Root onOpenChange={(open) => open && onSelect()}>
      <ContextMenu.Trigger asChild>
        <div>{children}</div>
      </ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="mac-menu">
          <ContextMenu.Item className="mac-menu-item" disabled={!!item.job} onSelect={onGenerate}>
            Generate Vector
          </ContextMenu.Item>
          <ContextMenu.Item className="mac-menu-item" disabled={item.shown === null} onSelect={onExport}>
            Export SVG…
          </ContextMenu.Item>
          <ContextMenu.Item className="mac-menu-item" disabled={!item.image.path} onSelect={onReveal}>
            Show in Finder
          </ContextMenu.Item>
          {redraw && (
            <>
              <ContextMenu.Separator className="mac-menu-separator" />
              <ContextMenu.Item className="mac-menu-item" disabled={!!item.redraw?.phase} onSelect={redraw.onRedraw}>
                Redraw with AI…
              </ContextMenu.Item>
              <ContextMenu.Item className="mac-menu-item" disabled={!item.redraw?.active} onSelect={redraw.onShowOriginal}>
                Show Original
              </ContextMenu.Item>
              <ContextMenu.Item className="mac-menu-item" disabled={!item.redraw?.active} onSelect={redraw.onRevert}>
                Revert to Original
              </ContextMenu.Item>
            </>
          )}
          <ContextMenu.Separator className="mac-menu-separator" />
          <ContextMenu.Item className="mac-menu-item" onSelect={onRemove}>
            Remove
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
