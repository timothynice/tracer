import { ImageOff, Plus, Trash2 } from "lucide-react";
import { Fragment, useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

import { failureText, MAX_SIDE } from "@/lib/limits";
import type { OpenFailure } from "@/platform/types";
import type { ImageItem } from "@/state/library";
import { ImageCard } from "./ImageCard";

export interface SidebarProps {
  items: ImageItem[];
  failed: OpenFailure[];
  selected: string | null;
  /** Under "Add Image": what can be opened here. */
  formats: string;
  /** Whether Downscale can be offered (the Mac app, a file with a path). */
  canDownscale: boolean;
  onAdd: () => void;
  onSelect: (id: string) => void;
  onSelectNext: (delta: 1 | -1) => void;
  onClear: () => void;
  onDownscale: (index: number) => void;
  onDismissFailure: (index: number) => void;
  /** Wraps each card, e.g. in its context menu. */
  wrapCard?: (item: ImageItem, card: ReactNode) => ReactNode;
}

const TOO_BIG = new Set(["too_many_pixels", "too_large"]);

export function Sidebar({ items, failed, selected, formats, canDownscale, onAdd, onSelect, onSelectNext, onClear, onDownscale, onDismissFailure, wrapCard = (_, card) => card }: SidebarProps) {
  // A selection made by the arrow keys, or a newly opened image, may be out of sight in a long list; so may a new failure.
  useEffect(() => {
    if (selected) document.getElementById(`image-${selected}`)?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);
  const failures = useRef<HTMLDivElement>(null);
  const seenFailures = useRef(failed.length);
  useEffect(() => {
    if (failed.length > seenFailures.current) failures.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" });
    seenFailures.current = failed.length;
  }, [failed.length]);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      onSelectNext(1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      onSelectNext(-1);
    }
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="p-2">
        <button type="button" onClick={onAdd} className="flex w-full items-center gap-3 rounded-lg bg-background/70 p-2.5 text-left transition-colors hover:bg-background">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-background shadow-sm">
            <Plus className="h-4 w-4" aria-hidden="true" />
          </span>
          <span className="min-w-0">
            <span className="block text-[13px] font-semibold">Add Image</span>
            <span className="block truncate text-[11px] text-muted-foreground">{formats}</span>
          </span>
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div role="listbox" aria-label="Image list" tabIndex={items.length ? 0 : -1} aria-activedescendant={selected ? `image-${selected}` : undefined} onKeyDown={onKeyDown} className={`space-y-3 px-3 ${items.length ? "pb-3" : ""}`}>
          {items.map((item) => (
            <Fragment key={item.image.id}>{wrapCard(item, <ImageCard item={item} selected={item.image.id === selected} onSelect={() => onSelect(item.image.id)} />)}</Fragment>
          ))}
        </div>
        <div ref={failures} className={`space-y-3 px-3 ${failed.length ? "pb-3" : ""}`}>
          {failed.map((f, i) => (
            <div key={`${f.path ?? f.name}-${i}`} role="group" aria-label={`${f.name} could not be opened`} className="rounded-lg bg-background/60 p-2.5">
              <div className="flex items-start gap-2">
                <ImageOff className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium">{f.name}</p>
                  <p className="text-[11px] leading-snug text-muted-foreground">{failureText(f)}</p>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {canDownscale && f.path && TOO_BIG.has(f.error.code) && (
                  <button type="button" className="mac-button h-7 px-2 text-[12px]" onClick={() => onDownscale(i)}>
                    Downscale to {MAX_SIDE} px
                  </button>
                )}
                <button type="button" className="mac-button h-7 px-2 text-[12px]" onClick={() => onDismissFailure(i)}>
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="border-t p-2">
        <button type="button" className="mac-ghost w-full" disabled={!items.length && !failed.length} onClick={onClear}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          Clear All
        </button>
      </div>
    </div>
  );
}
