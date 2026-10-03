import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmptyState } from "./EmptyState";
import { SAMPLES } from "@/lib/samples";

describe("EmptyState", () => {
  it("asks for images in the formats it is given and opens the panel", () => {
    const onOpen = vi.fn();
    render(<EmptyState formats="PNG or BMP" onOpen={onOpen} onSample={vi.fn()} />);
    expect(screen.getByText("Drop images here")).toBeInTheDocument();
    expect(screen.getByText("PNG or BMP, up to 2048 px a side")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open…" }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("names the sample chosen", () => {
    const onSample = vi.fn();
    render(<EmptyState formats="PNG" onOpen={vi.fn()} onSample={onSample} />);
    fireEvent.click(screen.getByRole("button", { name: SAMPLES[0].label }));
    expect(onSample).toHaveBeenCalledWith(SAMPLES[0].name);
  });
});
