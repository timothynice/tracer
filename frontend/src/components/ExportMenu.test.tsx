import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExportMenu } from "./ExportMenu";

function open() {
  fireEvent.keyDown(screen.getByRole("button", { name: "More export options" }), { key: "Enter" });
}

describe("ExportMenu", () => {
  it("exports the SVG, and is off with nothing to export", () => {
    const onExport = vi.fn();
    const { rerender } = render(<ExportMenu canExport anyVector onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Export SVG/ }));
    expect(onExport).toHaveBeenCalledWith("svg", 1);
    rerender(<ExportMenu canExport={false} anyVector={false} onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Export SVG/ })).toBeDisabled();
  });

  it("offers PNG at three sizes, Copy SVG and Export All", () => {
    const onExport = vi.fn();
    const onCopy = vi.fn();
    const onExportAll = vi.fn();
    render(<ExportMenu canExport anyVector onExport={onExport} onCopy={onCopy} onExportAll={onExportAll} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /PNG at 2×/ }));
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Copy SVG/ }));
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Export All/ }));
    expect(onExport).toHaveBeenCalledWith("png", 2);
    expect(onCopy).toHaveBeenCalledOnce();
    expect(onExportAll).toHaveBeenCalledOnce();
  });
});
