import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { paramsKey, specsFor } from "@/lib/schema";
import type { ImageItem } from "@/state/library";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";
import { VectorizePanel } from "./VectorizePanel";

const key = paramsKey({ detail: 6, min_region: 8 });
const item: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 4, height: 4, format: "PNG", previewUrl: "blob:a" },
  preset: "balanced",
  params: { detail: 6, min_region: 8 },
  traces: { [key]: { svg: "<svg/>", elapsedMs: 1530, stats: { paths: 16, nodes: 208, bytes: 4544 } } },
  shown: key,
  job: null,
  error: null,
  auto: null,
};

describe("VectorizePanel", () => {
  it("heads the panel, folds the advanced options and states what the trace is", () => {
    const onParam = vi.fn();
    render(
      <VectorizePanel item={item} catalog={{ engine: VEXEL, presets: VEXEL_PRESETS }} specs={specsFor(VEXEL)} invalidField={null} onPick={vi.fn()} onParam={onParam} onGenerate={vi.fn()} onCancel={vi.fn()} exportMenu={<span>export</span>} />,
    );
    expect(screen.getByRole("heading", { name: "Vectorize" })).toBeInTheDocument();
    expect(screen.getByText("16 shapes · 208 nodes · 4.4 KB · 1.5 s")).toBeInTheDocument();
    expect(screen.queryByText("Shapes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Advanced Options" }));
    expect(screen.getByText("Shapes")).toBeInTheDocument();
    expect(screen.getByText("export")).toBeInTheDocument();
  });
});
