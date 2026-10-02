import { useCallback, useEffect, useMemo, useState } from "react";

import { EMPTY_INSPECTOR, tinyShapes, type InspectorState } from "@/components/Inspector";
import { parseSvg } from "@/lib/svgdoc";

/** The layer inspector's state for one SVG: shapes hidden by hand or by size, and the SVG to export without them. */
export function useLayerInspector(svg: string | undefined) {
  const [state, setState] = useState<InspectorState>(EMPTY_INSPECTOR);
  const patch = useCallback((p: Partial<InspectorState>) => setState((prev) => ({ ...prev, ...p })), []);
  const doc = useMemo(() => (svg ? parseSvg(svg) : null), [svg]);
  // a new trace renumbers the shapes, so per-shape state cannot carry over
  useEffect(() => setState((prev) => ({ ...prev, hidden: new Set(), highlight: null, minArea: 0 })), [svg]);
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
