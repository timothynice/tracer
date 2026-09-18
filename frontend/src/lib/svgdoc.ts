/** A parsed view of a traced SVG: its shapes, their anchors, and a way back to a string.
 *
 * Everything here works on the markup the engines emit — absolute `M/L/C/Z`
 * paths plus `rect`, `circle`, `ellipse`, `polygon` — and never needs layout,
 * so it behaves the same in a test environment as in the browser.
 */

export interface Shape {
  /** Index in paint order; also its identity for hiding and highlighting. */
  index: number;
  tag: string;
  /** What the shape is painted with: a hex colour, or a gradient/filter reference. */
  fill: string;
  paint: "solid" | "gradient" | "none";
  opacity: number;
  filtered: boolean;
  anchors: [number, number][];
  /** Axis-aligned bounds of the anchors: x, y, width, height in user units. */
  bounds: [number, number, number, number];
  area: number;
}

export interface SvgDoc {
  width: number;
  height: number;
  shapes: Shape[];
  /** The markup again, with the given shape indices left out. */
  render(hidden: ReadonlySet<number>): string;
}

const SHAPE_TAGS = new Set(["path", "rect", "circle", "ellipse", "polygon", "polyline", "line"]);
const NUM = /-?\d*\.?\d+(?:e[-+]?\d+)?/gi;

/** On-curve points of an absolute path: the ones a user would drag. */
export function pathAnchors(d: string): [number, number][] {
  const out: [number, number][] = [];
  const tokens = d.match(/[MLHVCSQTAZmlhvcsqtaz]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? [];
  let i = 0;
  let cx = 0;
  let cy = 0;
  let cmd = "";
  const num = () => Number(tokens[i++]);
  while (i < tokens.length) {
    const t = tokens[i];
    if (/[a-z]/i.test(t)) {
      cmd = t;
      i++;
      if (cmd === "Z" || cmd === "z") continue;
    }
    const rel = cmd === cmd.toLowerCase();
    const upper = cmd.toUpperCase();
    // Skip the control points; keep only where the pen lands.
    const skip = { M: 0, L: 0, T: 0, H: 0, V: 0, S: 2, Q: 2, C: 4, A: 5 }[upper];
    if (skip === undefined) {
      i++;
      continue;
    }
    for (let s = 0; s < skip; s++) num();
    if (upper === "H") {
      const x = num();
      cx = rel ? cx + x : x;
    } else if (upper === "V") {
      const y = num();
      cy = rel ? cy + y : y;
    } else {
      const x = num();
      const y = num();
      cx = rel ? cx + x : x;
      cy = rel ? cy + y : y;
    }
    if (Number.isFinite(cx) && Number.isFinite(cy)) out.push([cx, cy]);
  }
  return out;
}

function attrNum(el: Element, name: string, fallback = 0): number {
  const v = Number(el.getAttribute(name));
  return Number.isFinite(v) ? v : fallback;
}

function anchorsOf(el: Element): [number, number][] {
  switch (el.tagName.toLowerCase()) {
    case "path":
      return pathAnchors(el.getAttribute("d") ?? "");
    case "rect": {
      const x = attrNum(el, "x");
      const y = attrNum(el, "y");
      const w = attrNum(el, "width");
      const h = attrNum(el, "height");
      return [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
      ];
    }
    case "circle": {
      const cx = attrNum(el, "cx");
      const cy = attrNum(el, "cy");
      const r = attrNum(el, "r");
      return [
        [cx, cy - r],
        [cx + r, cy],
        [cx, cy + r],
        [cx - r, cy],
      ];
    }
    case "ellipse": {
      const cx = attrNum(el, "cx");
      const cy = attrNum(el, "cy");
      const rx = attrNum(el, "rx");
      const ry = attrNum(el, "ry");
      return [
        [cx, cy - ry],
        [cx + rx, cy],
        [cx, cy + ry],
        [cx - rx, cy],
      ];
    }
    default: {
      const pts = (el.getAttribute("points") ?? "").match(NUM)?.map(Number) ?? [];
      const out: [number, number][] = [];
      for (let i = 0; i + 1 < pts.length; i += 2) out.push([pts[i], pts[i + 1]]);
      return out;
    }
  }
}

function boundsOf(anchors: [number, number][]): [number, number, number, number] {
  if (!anchors.length) return [0, 0, 0, 0];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of anchors) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return [minX, minY, maxX - minX, maxY - minY];
}

export function parseSvg(markup: string): SvgDoc | null {
  const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
  const root = doc.documentElement;
  if (!root || root.tagName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return null;

  const viewBox = (root.getAttribute("viewBox") ?? "").match(NUM)?.map(Number) ?? [];
  const width = viewBox[2] || attrNum(root, "width", 0);
  const height = viewBox[3] || attrNum(root, "height", 0);

  const elements: Element[] = [];
  for (const child of Array.from(root.children)) {
    if (child.tagName.toLowerCase() === "defs") continue;
    if (child.tagName.toLowerCase() === "g") elements.push(...Array.from(child.children).filter((c) => SHAPE_TAGS.has(c.tagName.toLowerCase())));
    else if (SHAPE_TAGS.has(child.tagName.toLowerCase())) elements.push(child);
  }

  const shapes: Shape[] = elements.map((el, index) => {
    const raw = el.getAttribute("fill") ?? "#000000";
    const stroke = el.getAttribute("stroke");
    const painted = raw === "none" && stroke ? stroke : raw;
    const anchors = anchorsOf(el);
    const bounds = boundsOf(anchors);
    return {
      index,
      tag: el.tagName.toLowerCase(),
      fill: painted,
      paint: painted === "none" ? "none" : painted.startsWith("url(") ? "gradient" : "solid",
      opacity: Number(el.getAttribute("fill-opacity") ?? el.getAttribute("opacity") ?? 1),
      filtered: el.hasAttribute("filter"),
      anchors,
      bounds,
      area: bounds[2] * bounds[3],
    };
  });

  return {
    width,
    height,
    shapes,
    render(hidden: ReadonlySet<number>): string {
      if (!hidden.size) return markup;
      const copy = new DOMParser().parseFromString(markup, "image/svg+xml");
      const live: Element[] = [];
      for (const child of Array.from(copy.documentElement.children)) {
        if (child.tagName.toLowerCase() === "defs") continue;
        if (child.tagName.toLowerCase() === "g") live.push(...Array.from(child.children).filter((c) => SHAPE_TAGS.has(c.tagName.toLowerCase())));
        else if (SHAPE_TAGS.has(child.tagName.toLowerCase())) live.push(child);
      }
      live.forEach((el, i) => hidden.has(i) && el.remove());
      return new XMLSerializer().serializeToString(copy);
    },
  };
}

/** A readable name for a shape, since traced output has no layer names. */
export function shapeLabel(s: Shape): string {
  const kind = s.tag === "path" ? "Path" : s.tag[0].toUpperCase() + s.tag.slice(1);
  return `${kind} ${s.index + 1}`;
}
