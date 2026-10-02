import { useCallback } from "react";
import { toast } from "sonner";

import { baseName, svgToPngBlob } from "@/lib/raster";
import { platform, type Settings } from "@/platform";
import { shownAnswer, type ImageItem, type LibraryState } from "@/state/library";

const fileName = (path: string) => path.split("/").pop() ?? path;

/** Export the selected image's vector (as edited in the layer inspector), copy it, or export every traced image. */
export function useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings) {
  const exportImage = useCallback(
    async (kind: "svg" | "png", scale: number) => {
      if (!item || !svg) return;
      const stem = baseName(item.image.name);
      try {
        const bytes = kind === "svg" ? new TextEncoder().encode(svg) : new Uint8Array(await (await svgToPngBlob(svg, item.image.width, item.image.height, scale)).arrayBuffer());
        const name = kind === "svg" ? `${stem}.svg` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
        const path = await platform.exportFile({ kind, imageId: item.image.id, name, bytes }, settings);
        if (path && platform.kind === "native" && !settings.revealAfterExport) {
          toast.success(`Exported ${fileName(path)}`, { action: { label: "Show in Finder", onClick: () => void platform.reveal(path) } });
        }
      } catch (err) {
        toast.error((err as Error).message);
      }
    },
    [item, svg, settings],
  );

  const exportAll = useCallback(async () => {
    const files = state.items.flatMap((i) => {
      const a = shownAnswer(i);
      return a ? [{ name: `${baseName(i.image.name)}.svg`, svg: a.svg }] : [];
    });
    if (!files.length) return;
    try {
      const written = await platform.exportAll(files, settings);
      if (written && platform.kind === "native") toast.success(`Exported ${written.length} ${written.length === 1 ? "file" : "files"}`);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [state.items, settings]);

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
