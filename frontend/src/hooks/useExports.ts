import { useCallback, useRef } from "react";
import { toast } from "sonner";

import { baseName, svgToPngBlob } from "@/lib/raster";
import { platform, type Settings } from "@/platform";
import { shownAnswer, type ImageItem, type LibraryState } from "@/state/library";

const fileName = (path: string) => path.split("/").pop() ?? path;

/** Export the selected image's vector (as edited in the layer inspector), copy it, or export every traced image. */
export function useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings) {
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
        // the selected image goes as it is on screen, with the layer inspector's edits; any other as its own trace
        const text = target === item ? svg : target ? shownAnswer(target)?.svg : undefined;
        if (!target || !text) return;
        const stem = baseName(target.image.name);
        try {
          const bytes = kind === "svg" ? new TextEncoder().encode(text) : new Uint8Array(await (await svgToPngBlob(text, target.image.width, target.image.height, scale)).arrayBuffer());
          const name = kind === "svg" ? `${stem}.svg` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
          const path = await platform.exportFile({ kind, imageId: target.image.id, name, bytes }, settings);
          if (path && platform.kind === "native" && !settings.revealAfterExport) {
            toast.success(`Exported ${fileName(path)}`, { action: { label: "Show in Finder", onClick: () => void platform.reveal(path) } });
          }
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [item, svg, settings], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
  );

  const exportAll = useCallback(
    () =>
      exclusive(async () => {
        const files = state.items.flatMap((i) => {
          const a = shownAnswer(i);
          // the selected image goes as it is on screen, with the layer inspector's edits
          return a ? [{ name: `${baseName(i.image.name)}.svg`, svg: i.image.id === item?.image.id && svg ? svg : a.svg }] : [];
        });
        if (!files.length) return;
        try {
          const written = await platform.exportAll(files, settings);
          if (written && platform.kind === "native") toast.success(`Exported ${written.length} ${written.length === 1 ? "file" : "files"}`);
        } catch (err) {
          toast.error((err as Error).message);
        }
      }),
    [state.items, item, svg, settings], // eslint-disable-line react-hooks/exhaustive-deps -- `exclusive` only touches a ref
  );

  const copySvg = useCallback(async () => {
    if (!svg) return;
    try {
      await platform.copyText(svg);
      toast.success("Copied SVG");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [svg]);

  return { exportImage, exportAll, copySvg };
}
