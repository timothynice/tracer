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
