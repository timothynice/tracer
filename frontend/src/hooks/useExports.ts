import { useCallback, useRef } from "react";
import { toast } from "sonner";

import { baseName, svgToPngBlob } from "@/lib/raster";
import { platform, type ExportKind, type Settings } from "@/platform";
import { revealInFinder } from "./reveal";
import { shownAnswer, type ExportMark, type ImageItem, type LibraryState } from "@/state/library";

const fileName = (path: string) => path.split("/").pop() ?? path;

/**
 * Export the selected image's vector (as edited in the layer inspector), copy it, or export every traced image.
 * `forImage` gives any other image's SVG as its own inspector state would export it.
 * `onExported` hears which traces went out, so closing the app knows what would be lost.
 */
export function useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings, onExported: (marks: ExportMark[]) => void = () => {}, forImage: (id: string, svg: string) => string = (_, v) => v) {
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
    (kind: ExportKind, scale: number, target: ImageItem | null = item) =>
      exclusive(async () => {
        // the selected image goes as it is on screen, with the layer inspector's edits; any other with its own.
        // By id: the context menu's item may be an older copy of the selected one (a state update since render).
        const text = target?.image.id === item?.image.id ? svg : target && shownAnswer(target) ? forImage(target.image.id, shownAnswer(target)!.svg) : undefined;
        if (!target || !text) return;
        const stem = baseName(target.image.name);
        try {
          // a PDF goes as the SVG's text: the app converts it
          const bytes = kind === "png" ? new Uint8Array(await (await svgToPngBlob(text, target.image.width, target.image.height, scale)).arrayBuffer()) : new TextEncoder().encode(text);
          const name = kind === "svg" ? `${stem}.svg` : kind === "pdf" ? `${stem}.pdf` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
          const path = await platform.exportFile({ kind, imageId: target.image.id, name, bytes }, settings);
          // the selected image's bytes came from what is on screen now, so its mark is the trace shown now
          const key = target.image.id === item?.image.id ? item.shown : target.shown;
          if (path && key) onExported([{ id: target.image.id, key }]);
          if (path && platform.kind === "native" && !settings.revealAfterExport) {
            toast.success(`Exported ${fileName(path)}`, { action: { label: "Show in Finder", onClick: () => void revealInFinder(path) } });
          }
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [item, svg, settings, onExported, forImage], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
  );

  const exportAll = useCallback(
    () =>
      exclusive(async () => {
        const traced = state.items.filter((i) => shownAnswer(i));
        // the selected image goes as it is on screen, with the layer inspector's edits
        const files = traced.map((i) => ({ id: i.image.id, name: `${baseName(i.image.name)}.svg`, svg: i.image.id === item?.image.id && svg ? svg : forImage(i.image.id, shownAnswer(i)!.svg) }));
        if (!files.length) return;
        try {
          const answer = await platform.exportAll(files, settings);
          if (!answer) return;
          // only the images that were written count as exported
          const lost = new Set(answer.failed.map((f) => f.id));
          const marks = traced.filter((i) => !lost.has(i.image.id)).map((i) => ({ id: i.image.id, key: i.shown! }));
          if (marks.length) onExported(marks);
          const { written, failed } = answer;
          const noun = (n: number) => `${n} ${n === 1 ? "file" : "files"}`;
          if (failed.length) {
            // one failure's message already names its file
            // a long list would make a tall toast
            const shown = failed.slice(0, 3).map((f) => `\u201c${f.name}\u201d`);
            const names = failed.length > 3 ? `${shown.join(", ")} and ${failed.length - 3} more` : shown.join(", ");
            toast.error(failed.length === 1 ? failed[0].message : `${failed.length} images could not be exported: ${names}. ${failed[0].message}`);
          } else if (platform.kind === "native") toast.success(`Exported ${noun(written.length)}`);
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [state.items, item, svg, settings, onExported, forImage], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
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
