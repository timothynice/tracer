import { useCallback, useMemo, useState } from "react";

import { EMPTY_INSPECTOR, tinyShapes, type InspectorState } from "@/components/Inspector";
import { parseSvg } from "@/lib/svgdoc";

const NONE: ReadonlySet<number> = new Set();

/** What belongs to one image: its speck threshold, and the hidden shapes and highlight of the one SVG they were made on. */
interface Entry {
  minArea: number;
  /** The SVG `hidden` and `highlight` index into; they are ignored for any other, since a trace renumbers the shapes. */
  svg: string | undefined;
  hidden: ReadonlySet<number>;
  highlight: number | null;
}

type Shared = Pick<InspectorState, "open" | "points" | "outlines">;

/**
 * The layer inspector's state for one image's SVG: shapes hidden by hand or by size, and the SVG to export without them.
 * The speck threshold is the image's, so a live re-trace does not bring the specks back into an export; the hidden
 * shapes and the highlight are the SVG's. The panel and the display toggles are the window's.
 */
export function useLayerInspector(svg: string | undefined, imageId = "") {
  const [shared, setShared] = useState<Shared>({ open: EMPTY_INSPECTOR.open, points: EMPTY_INSPECTOR.points, outlines: EMPTY_INSPECTOR.outlines });
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const entry = entries[imageId];
  const own = entry && entry.svg === svg;
  const hidden = own ? entry.hidden : NONE;
  const highlight = own ? entry.highlight : null;
  const minArea = entry?.minArea ?? 0;
  const state = useMemo<InspectorState>(() => ({ ...shared, hidden, highlight, minArea }), [shared, hidden, highlight, minArea]);

  const patch = useCallback(
    (p: Partial<InspectorState>) => {
      const { open, points, outlines, hidden: h, highlight: hl, minArea: m } = p;
      if (open !== undefined || points !== undefined || outlines !== undefined) {
        setShared((prev) => ({ open: open ?? prev.open, points: points ?? prev.points, outlines: outlines ?? prev.outlines }));
      }
      if (h === undefined && hl === undefined && m === undefined) return;
      setEntries((prev) => {
        const cur = prev[imageId];
        const mine = cur && cur.svg === svg;
        return { ...prev, [imageId]: { minArea: m ?? cur?.minArea ?? 0, svg, hidden: h ?? (mine ? cur.hidden : NONE), highlight: hl !== undefined ? hl : mine ? cur.highlight : null } };
      });
    },
    [svg, imageId],
  );

  const doc = useMemo(() => (svg ? parseSvg(svg) : null), [svg]);
  const dropped = useMemo(() => {
    if (!doc) return new Set<number>();
    const out = new Set(state.hidden);
    for (const i of tinyShapes(doc, state.minArea)) out.add(i);
    return out;
  }, [doc, state.hidden, state.minArea]);
  const exportSvg = useMemo(() => (doc && dropped.size ? doc.render(dropped) : svg), [doc, dropped, svg]);
  const liveState = useMemo(() => ({ ...state, hidden: dropped }), [state, dropped]);
  return { doc, state, patch, dropped, exportSvg, liveState };
}
