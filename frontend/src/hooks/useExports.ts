import { useCallback, useRef } from "react";
import { toast } from "sonner";

import { baseName, svgToPngBlob } from "@/lib/raster";
import { platform, type Settings } from "@/platform";
import { shownAnswer, type ExportMark, type ImageItem, type LibraryState } from "@/state/library";

const fileName = (path: string) => path.split("/").pop() ?? path;

/**
 * Export the selected image's vector (as edited in the layer inspector), copy it, or export every traced image.
 * `onExported` hears which traces went out, so closing the app knows what would be lost.
 */
export function useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings, onExported: (marks: ExportMark[]) => void = () => {}) {
  // One save panel at a time: an export asked for while another is open is ignored.
  const busy = useRef(false);
  const exclusive = async (run: () => Promise<void>) => {
    if (busy.current) return;
    busy.current = true;
    try {
      await run();
    } finally {
      busy.current = false;
    }
  };

  const exportImage = useCallback(
    (kind: "svg" | "png", scale: number, target: ImageItem | null = item) =>
      exclusive(async () => {
        // the selected image goes as it is on screen, with the layer inspector's edits; any other as its own trace.
        // By id: the context menu's item may be an older copy of the selected one (a state update since render).
        const text = target?.image.id === item?.image.id ? svg : target ? shownAnswer(target)?.svg : undefined;
        if (!target || !text) return;
        const stem = baseName(target.image.name);
        try {
          const bytes = kind === "svg" ? new TextEncoder().encode(text) : new Uint8Array(await (await svgToPngBlob(text, target.image.width, target.image.height, scale)).arrayBuffer());
          const name = kind === "svg" ? `${stem}.svg` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
          const path = await platform.exportFile({ kind, imageId: target.image.id, name, bytes }, settings);
          // the selected image's bytes came from what is on screen now, so its mark is the trace shown now
          const key = target.image.id === item?.image.id ? item.shown : target.shown;
          if (path && key) onExported([{ id: target.image.id, key }]);
          if (path && platform.kind === "native" && !settings.revealAfterExport) {
            toast.success(`Exported ${fileName(path)}`, { action: { label: "Show in Finder", onClick: () => void platform.reveal(path) } });
          }
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [item, svg, settings, onExported], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
  );

  const exportAll = useCallback(
    () =>
      exclusive(async () => {
        const traced = state.items.filter((i) => shownAnswer(i));
        // the selected image goes as it is on screen, with the layer inspector's edits
        const files = traced.map((i) => ({ name: `${baseName(i.image.name)}.svg`, svg: i.image.id === item?.image.id && svg ? svg : shownAnswer(i)!.svg }));
        if (!files.length) return;
        try {
          const written = await platform.exportAll(files, settings);
          if (written) onExported(traced.map((i) => ({ id: i.image.id, key: i.shown! })));
          if (written && platform.kind === "native") toast.success(`Exported ${written.length} ${written.length === 1 ? "file" : "files"}`);
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [state.items, item, svg, settings, onExported], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
  );

  const copySvg = useCallback(async () => {
    if (!svg) return;
    try {
      await platform.copyText(svg);
      if (item?.shown) onExported([{ id: item.image.id, key: item.shown }]);
      toast.success("Copied SVG");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [svg, item, onExported]);

  return { exportImage, exportAll, copySvg };
}
