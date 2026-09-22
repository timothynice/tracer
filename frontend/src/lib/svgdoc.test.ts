import { parseSvg, pathAnchors, shapeLabel } from "./svgdoc";

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 48">
<defs><linearGradient id="g1"><stop offset="0" stop-color="#fff"/></linearGradient></defs>
<rect x="0" y="0" width="64" height="48" fill="#eeeeee"/>
<path d="M8 8L24 8C30 8 30 20 24 20L8 20Z" fill="url(#g1)" fill-opacity="0.5"/>
<circle cx="48" cy="24" r="6" fill="#ff0000" filter="url(#s1)"/></svg>`;

test("reads the anchors a user would drag, not the control points", () => {
  // M, the L endpoint, each C endpoint, the closing L — six, not the ten
  // coordinate pairs the markup contains.
  expect(pathAnchors("M8 8L24 8C30 8 30 20 24 20L8 20Z")).toEqual([
    [8, 8],
    [24, 8],
    [24, 20],
    [8, 20],
  ]);
  expect(pathAnchors("M0 0h10v10")).toEqual([[0, 0], [10, 0], [10, 10]]);
  // Vexel writes circular arcs: the radius, rotation and flags are not anchors,
  // the arc's end is.
  expect(pathAnchors("M10 50A40 40 0 0 1 90 50L90 60Z")).toEqual([
    [10, 50],
    [90, 50],
    [90, 60],
  ]);
});

test("describes each painted shape and skips defs", () => {
  const doc = parseSvg(SVG)!;
  expect([doc.width, doc.height]).toEqual([64, 48]);
  expect(doc.shapes.map((s) => s.tag)).toEqual(["rect", "path", "circle"]);
  expect(doc.shapes[0].area).toBe(64 * 48);
  expect(doc.shapes[1].paint).toBe("gradient");
  expect(doc.shapes[1].opacity).toBe(0.5);
  expect(doc.shapes[2].filtered).toBe(true);
  expect(doc.shapes[2].bounds).toEqual([42, 18, 12, 12]);
  expect(shapeLabel(doc.shapes[2])).toBe("Circle 3");
});

test("rendering without a shape removes it and leaves the rest untouched", () => {
  const doc = parseSvg(SVG)!;
  expect(doc.render(new Set())).toBe(SVG); // no change, no reserialisation
  const out = doc.render(new Set([1]));
  expect(out).not.toContain("M8 8L24 8");
  expect(out).toContain("<circle");
  expect(out).toContain("linearGradient"); // defs survive
  expect(parseSvg(out)!.shapes).toHaveLength(2);
});

test("malformed markup is rejected rather than half-parsed", () => {
  expect(parseSvg("<svg><path d='M0 0'")).toBeNull();
  expect(parseSvg("not svg at all")).toBeNull();
});

test("outlines carry the real geometry, not a chord through the anchors", () => {
  const doc = parseSvg(SVG)!;
  // A curve drawn as a polyline through its anchors cuts straight across the
  // corner; the outline has to be the shape's own `d`.
  expect(doc.shapes[1].outline).toBe('<path d="M8 8L24 8C30 8 30 20 24 20L8 20Z" fill="none"/>');
  expect(doc.shapes[2].outline).toBe('<circle cx="48" cy="24" r="6" fill="none"/>');
  // Nothing that could reach into defs or repaint the canvas comes along.
  expect(doc.shapes[1].outline).not.toContain("url(");
  expect(doc.shapes[2].outline).not.toContain("filter");
});

test("a transform moves the anchors and travels with the outline", () => {
  // Vexel emits rotated ellipses as `rotate(a cx cy)`. Dropping the transform
  // draws an unrotated ghost the renderer never paints, and leaves the anchors
  // on it — which is what a rotated sticker looked like.
  const rotated = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">
<ellipse cx="200" cy="230" rx="20" ry="60" fill="#fff" transform="rotate(-90 200 230)"/></svg>`;
  const s = parseSvg(rotated)!.shapes[0];
  expect(s.outline).toContain('transform="rotate(-90 200 230)"');
  // Unrotated the anchors would be 60 above and below the centre; rotated by
  // -90 they are 60 to the left and right of it.
  const xs = s.anchors.map(([x]) => Math.round(x)).sort((a, b) => a - b);
  const ys = s.anchors.map(([, y]) => Math.round(y)).sort((a, b) => a - b);
  expect(xs[0]).toBe(140);
  expect(xs[3]).toBe(260);
  expect(ys[0]).toBe(210);
  expect(ys[3]).toBe(250);
  expect(s.bounds).toEqual([140, 210, 120, 40]);
});

test("translate, scale and matrix all land where the renderer puts them", () => {
  const doc = parseSvg(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect x="0" y="0" width="10" height="10" fill="#000" transform="translate(5 7)"/>
<rect x="0" y="0" width="10" height="10" fill="#000" transform="scale(2 3)"/>
<rect x="0" y="0" width="10" height="10" fill="#000" transform="matrix(1 0 0 1 4 4)"/></svg>`)!;
  expect(doc.shapes[0].bounds).toEqual([5, 7, 10, 10]);
  expect(doc.shapes[1].bounds).toEqual([0, 0, 20, 30]);
  expect(doc.shapes[2].bounds).toEqual([4, 4, 10, 10]);
});


test("a <use> of a defined shape is that shape, moved, with its own paint", () => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<defs><circle cx="32" cy="32" r="12" id="u1"/></defs>
<use href="#u1" fill="#2b9d8f"/><use href="#u1" x="64" y="0" fill="#ff8800"/></svg>`;
  const doc = parseSvg(svg)!;
  expect(doc.shapes.map((s) => s.tag)).toEqual(["circle", "circle"]);
  expect(doc.shapes.map((s) => s.fill)).toEqual(["#2b9d8f", "#ff8800"]);
  expect(doc.shapes[1].bounds).toEqual([84, 20, 24, 24]);
  expect(doc.shapes[1].outline).toContain('translate(64 0)');
  expect(doc.shapes[1].outline).toContain('r="12"');
  expect(shapeLabel(doc.shapes[1])).toBe("Circle 2");
  // hiding the second copy removes its <use>, not the definition
  const hidden = doc.render(new Set([1]));
  expect(hidden).toContain('id="u1"');
  expect(hidden.match(/<use/g)?.length).toBe(1);
});
