import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DriftVerdict } from "@/platform/types";
import { DriftCheck, VerdictChip } from "./DriftCheck";

const original = { id: "a", name: "a.png", path: null, width: 64, height: 32, format: "PNG", previewUrl: "blob:a" };
const redraw = { ...original, id: "r", width: 128, height: 64, previewUrl: "blob:r" };

describe("DriftCheck", () => {
  it("shows the verdict, the two numbers and the three choices over the two images", () => {
    const decide = { onUse: vi.fn(), onTryAgain: vi.fn(), onDiscard: vi.fn() };
    render(<DriftCheck original={original} redraw={redraw} drift={{ edgeF1: 0.59, deltaE: 3.8, verdict: "large" }} mode="split" onModeChange={vi.fn()} decide={decide} />);
    const region = screen.getByRole("region", { name: "Drift check" });
    expect(within(region).getByText("Large")).toBeInTheDocument();
    expect(within(region).getByText("Edges matched: 59 %")).toBeInTheDocument();
    expect(within(region).getByText("Colour shift: ΔE 3.8")).toBeInTheDocument();
    fireEvent.click(within(region).getByRole("button", { name: "Use redraw" }));
    fireEvent.click(within(region).getByRole("button", { name: "Try again" }));
    fireEvent.click(within(region).getByRole("button", { name: "Discard" }));
    expect([decide.onUse, decide.onTryAgain, decide.onDiscard].map((f) => f.mock.calls.length)).toEqual([1, 1, 1]);
    expect(screen.getByAltText("Source raster")).toHaveAttribute("src", "blob:a");
    expect(screen.getByAltText("AI redraw")).toHaveAttribute("src", "blob:r");
  });

  it("never shows a Noticeable edge F1 of 0.946 as 95 %", () => {
    render(<DriftCheck original={original} redraw={redraw} drift={{ edgeF1: 0.946, deltaE: 3.04, verdict: "noticeable" }} mode="split" onModeChange={vi.fn()} decide={{ onUse: vi.fn(), onTryAgain: vi.fn(), onDiscard: vi.fn() }} />);
    expect(screen.getByText("Noticeable")).toBeInTheDocument();
    expect(screen.getByText("Edges matched: 94 %")).toBeInTheDocument();
    expect(screen.getByText("Colour shift: ΔE 3.0")).toBeInTheDocument();
  });

  it("reviewing the redraw in use offers only Done", () => {
    const onClose = vi.fn();
    render(<DriftCheck original={original} redraw={redraw} drift={{ edgeF1: 0.97, deltaE: 1.1, verdict: "close" }} mode="side" onModeChange={vi.fn()} onClose={onClose} />);
    expect(screen.queryByRole("button", { name: "Use redraw" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("gives each verdict its tone as a dot and a tint, never a one-sided border", () => {
    const tones: Record<DriftVerdict, string> = { close: "success", noticeable: "warning", large: "destructive" };
    for (const [verdict, tone] of Object.entries(tones) as [DriftVerdict, string][]) {
      const { container, unmount } = render(<VerdictChip verdict={verdict} />);
      const chip = container.firstElementChild as HTMLElement;
      expect(chip.className).toContain(`bg-${tone}/10`);
      expect(chip.querySelector(`.bg-${tone}`)).not.toBeNull();
      expect(container.innerHTML).not.toMatch(/border-[lrtbse]-|border-l\b|border-r\b/);
      unmount();
    }
  });
});
