import { fireEvent, render, screen } from "@testing-library/react";

import { EMPTY_INSPECTOR, Inspector, tinyShapes, type InspectorState } from "./Inspector";
import { parseSvg } from "@/lib/svgdoc";

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 48">
<rect x="0" y="0" width="64" height="48" fill="#eeeeee"/>
<path d="M8 8L24 8L24 20L8 20Z" fill="#2a9d8f"/>
<rect x="60" y="44" width="2" height="2" fill="#ff0000"/></svg>`;

const doc = parseSvg(SVG)!;
const state = (patch: Partial<InspectorState> = {}) => ({ ...EMPTY_INSPECTOR, open: true, ...patch });

test("lists every shape with its anchor count", () => {
  render(<Inspector doc={doc} bytes={512} state={state()} onChange={() => {}} />);
  expect(screen.getByText("Rect 1")).toBeInTheDocument();
  expect(screen.getByText("Path 2")).toBeInTheDocument();
  expect(screen.getByText("Rect 3")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument(); // 4 + 4 + 4 anchors
});

test("hiding a shape reports it back", () => {
  const onChange = vi.fn();
  render(<Inspector doc={doc} bytes={512} state={state()} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "Hide Path 2" }));
  expect(onChange).toHaveBeenCalledWith({ hidden: new Set([1]) });
});

test("the speck threshold picks out only the small shapes", () => {
  // The 2x2 speck goes; the 16x12 path and the full-canvas rect stay.
  expect(tinyShapes(doc, 0)).toEqual([]);
  expect(tinyShapes(doc, 10)).toEqual([2]);
  expect(tinyShapes(doc, 200)).toEqual([1, 2]);
});

test("says what the cleanup will actually drop", () => {
  render(<Inspector doc={doc} bytes={512} state={state({ minArea: 10 })} onChange={() => {}} />);
  expect(screen.getByText(/1 shape will be left out/)).toBeInTheDocument();
});
