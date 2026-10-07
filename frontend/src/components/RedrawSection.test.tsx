import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { NO_REDRAW, type ImageItem } from "@/state/library";
import { RedrawChip, RedrawSection } from "./RedrawSection";

const base: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 64, height: 64, format: "PNG", previewUrl: "blob:a" },
  preset: "auto",
  params: {},
  traces: {},
  shown: null,
  exported: null,
  job: null,
  error: null,
  errorKey: null,
  auto: null,
};
const handlers = () => ({ onRedraw: vi.fn(), onCancel: vi.fn() });
const HINT = "This image is small — an AI redraw may trace cleaner.";

describe("RedrawSection", () => {
  it("hints only for a rough image, and only while suggestions are on", () => {
    const { rerender } = render(<RedrawSection item={{ ...base, rough: { rough: true, reason: "small" } }} suggest {...handlers()} />);
    const hint = screen.getByRole("note");
    expect(hint).toHaveTextContent(HINT);
    // the dot on a muted tint, never a one-sided border
    expect(hint.className).toContain("mac-tint");
    expect(hint.querySelector(".mac-dot")).not.toBeNull();
    expect(hint.className).not.toMatch(/border-l|border-r|border-t|border-b/);
    rerender(<RedrawSection item={{ ...base, rough: { rough: true, reason: "small" } }} suggest={false} {...handlers()} />);
    expect(screen.queryByText(HINT)).toBeNull();
    rerender(<RedrawSection item={{ ...base, rough: { rough: false, reason: null } }} suggest {...handlers()} />);
    expect(screen.queryByRole("note")).toBeNull();
    rerender(<RedrawSection item={base} suggest {...handlers()} />);
    expect(screen.queryByRole("note")).toBeNull();
    // not while a redraw runs or waits for its decision
    const rough = { ...base, rough: { rough: true, reason: "small" as const } };
    rerender(<RedrawSection item={{ ...rough, redraw: { ...NO_REDRAW, phase: "drawing" } }} suggest {...handlers()} />);
    expect(screen.queryByRole("note")).toBeNull();
    const pending = { redraw: { ...base.image, id: "r" }, drift: { edgeF1: 0.9, deltaE: 3, verdict: "noticeable" as const } };
    rerender(<RedrawSection item={{ ...rough, redraw: { ...NO_REDRAW, pending } }} suggest {...handlers()} />);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("always offers Redraw with AI…, and Cancel while it runs", () => {
    const h = handlers();
    const { rerender } = render(<RedrawSection item={base} suggest {...h} />);
    fireEvent.click(screen.getByRole("button", { name: "Redraw with AI…" }));
    expect(h.onRedraw).toHaveBeenCalled();
    rerender(<RedrawSection item={{ ...base, redraw: { ...NO_REDRAW, phase: "drawing" } }} suggest {...h} />);
    expect(screen.getByText("Redrawing with AI…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Redraw with AI…" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Redraw" }));
    expect(h.onCancel).toHaveBeenCalled();
  });

  it("does not hint at a redrawn image again, and leaves the chip to the header", () => {
    render(<RedrawSection item={{ ...base, rough: { rough: true, reason: "small" }, redraw: { ...NO_REDRAW, active: true } }} suggest {...handlers()} />);
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByText("AI redraw")).toBeNull();
    expect(screen.queryByRole("button", { name: "Show Original" })).toBeNull();
  });

  it("the chip marks a redrawn image and offers the original back; it is nothing on any other", () => {
    const onShowOriginal = vi.fn();
    const onRevert = vi.fn();
    const { rerender } = render(<RedrawChip item={{ ...base, redraw: { ...NO_REDRAW, active: true } }} onShowOriginal={onShowOriginal} onRevert={onRevert} />);
    expect(screen.getByText("AI redraw")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show Original" }));
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect([onShowOriginal.mock.calls.length, onRevert.mock.calls.length]).toEqual([1, 1]);
    rerender(<RedrawChip item={base} onShowOriginal={onShowOriginal} onRevert={onRevert} />);
    expect(screen.queryByText("AI redraw")).toBeNull();
  });

  it("shows a failed redraw in the app's words", () => {
    render(<RedrawSection item={{ ...base, redraw: { ...NO_REDRAW, error: new ApiError("offline", "Studi0Trace could not reach OpenAI. Check your internet connection.", 503) } }} suggest {...handlers()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Studi0Trace could not reach OpenAI. Check your internet connection.");
  });

  it("words a failure by its code, never by the server's message, and says nothing of a cancel", () => {
    const err = (code: string) => ({ ...base, redraw: { ...NO_REDRAW, error: new ApiError(code, "sk-secret-123 leaked", 500) } });
    const { rerender } = render(<RedrawSection item={err("invalid_key")} suggest {...handlers()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("OpenAI did not accept your API key.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("sk-secret");
    rerender(<RedrawSection item={err("not_allowed")} suggest {...handlers()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("OpenAI did not allow this key to create images.");
    rerender(<RedrawSection item={err("engine_crashed")} suggest {...handlers()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The redraw stopped unexpectedly.");
    rerender(<RedrawSection item={err("cancelled")} suggest {...handlers()} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
