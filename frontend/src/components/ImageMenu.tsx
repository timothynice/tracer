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
}

/** A right click on an image card: it is selected, as in Finder, and offers what applies to it. */
export function ImageMenu({ item, children, onSelect, onGenerate, onExport, onReveal, onRemove }: ImageMenuProps) {
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
          <ContextMenu.Separator className="mac-menu-separator" />
          <ContextMenu.Item className="mac-menu-item" onSelect={onRemove}>
            Remove
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
