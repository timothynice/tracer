import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TitleBar } from "./TitleBar";

describe("TitleBar", () => {
  it("names the app, is a drag region and toggles the panes", () => {
    const onToggleSidebar = vi.fn();
    const onSettings = vi.fn();
    const { container } = render(<TitleBar native sidebar inspector={false} onToggleSidebar={onToggleSidebar} onToggleInspector={vi.fn()} onSettings={onSettings} />);
    expect(screen.getByRole("heading", { name: "Studi0Trace" })).toBeInTheDocument();
    expect(screen.getByText("Turn images into clean vectors")).toBeInTheDocument();
    const bar = container.querySelector("header")!;
    expect(bar.getAttribute("data-tauri-drag-region")).toBe("deep");
    expect(bar.style.paddingLeft).toBe("88px");
    expect(screen.getByRole("button", { name: "Show sidebar" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Show inspector" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(onToggleSidebar).toHaveBeenCalledOnce();
    expect(onSettings).toHaveBeenCalledOnce();
  });

  it("leaves no room for traffic lights in a browser", () => {
    const { container } = render(<TitleBar native={false} sidebar inspector onToggleSidebar={vi.fn()} onToggleInspector={vi.fn()} onSettings={vi.fn()} />);
    expect(container.querySelector("header")!.style.paddingLeft).toBe("16px");
  });
});
