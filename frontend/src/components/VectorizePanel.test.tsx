import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { paramsKey, specsFor } from "@/lib/schema";
import type { ImageItem } from "@/state/library";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";
import { VectorizePanel } from "./VectorizePanel";

// sixteen painted shapes: one path, a rect, a circle and thirteen copies of a defined path, none of them counted from the server's path tally
const SVG16 = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><path id="t" d="M0 0L4 0L4 4Z"/></defs><path d="M0 0L2 0L2 2L0 2Z"/><rect width="4" height="4"/><circle r="2"/>${Array.from({ length: 13 }, (_, i) => `<use href="#t" x="${i}" y="1"/>`).join("")}</svg>`;
const key = paramsKey({ detail: 6, min_region: 8 });
const item: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 4, height: 4, format: "PNG", previewUrl: "blob:a" },
  preset: "balanced",
  params: { detail: 6, min_region: 8 },
  traces: { [key]: { svg: SVG16, elapsedMs: 1530, stats: { paths: 1, nodes: 4, bytes: 4544 } } },
  shown: key,
  exported: null,
  job: null,
  error: null,
  errorKey: null,
  auto: null,
};

describe("VectorizePanel", () => {
  it("heads the panel, folds the advanced options and states what the trace is", () => {
    const onParam = vi.fn();
    render(
      <VectorizePanel item={item} catalog={{ engine: VEXEL, presets: VEXEL_PRESETS }} specs={specsFor(VEXEL)} invalidField={null} onPick={vi.fn()} onParam={onParam} onGenerate={vi.fn()} onCancel={vi.fn()} exportMenu={<span>export</span>} />,
    );
    expect(screen.getByRole("heading", { name: "Vectorize" })).toBeInTheDocument();
    expect(screen.getByText("16 shapes · 43 nodes · 4.4 KB · 1.5 s")).toBeInTheDocument();
    expect(screen.queryByText("Shapes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Advanced Options" }));
    expect(screen.getByText("Shapes")).toBeInTheDocument();
    expect(screen.getByText("export")).toBeInTheDocument();
  });

  it("opens Advanced Options when the server refuses a field after the panel is up", () => {
    const props = { item, catalog: { engine: VEXEL, presets: VEXEL_PRESETS }, specs: specsFor(VEXEL), onPick: vi.fn(), onParam: vi.fn(), onGenerate: vi.fn(), onCancel: vi.fn(), exportMenu: null };
    const { rerender } = render(<VectorizePanel {...props} invalidField={null} />);
    expect(screen.queryByText("Shapes")).toBeNull();
    rerender(<VectorizePanel {...props} invalidField="detail" />);
    expect(screen.getByText("Shapes")).toBeInTheDocument();
  });
  it("says one shape, not 1 shapes", () => {
    const one: ImageItem = { ...item, traces: { [key]: { svg: '<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>', elapsedMs: 10, stats: { paths: 0, nodes: 0, bytes: 60 } } } };
    render(<VectorizePanel item={one} catalog={{ engine: VEXEL, presets: VEXEL_PRESETS }} specs={specsFor(VEXEL)} invalidField={null} onPick={vi.fn()} onParam={vi.fn()} onGenerate={vi.fn()} onCancel={vi.fn()} exportMenu={null} />);
    expect(screen.getByText("1 shape · 0 nodes · 60 B · 10 ms")).toBeInTheDocument();
  });
});
