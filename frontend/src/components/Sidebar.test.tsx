import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { ImageItem } from "@/state/library";
import { Sidebar, type SidebarProps } from "./Sidebar";

const item = (id: string, patch: Partial<ImageItem> = {}): ImageItem => ({
  image: { id, name: `${id}.png`, path: `/p/${id}.png`, width: 1200, height: 800, format: "PNG", previewUrl: `blob:${id}` },
  preset: "auto",
  params: {},
  traces: {},
  shown: null,
  exported: null,
  job: null,
  error: null,
  auto: null,
  ...patch,
});

function setup(patch: Partial<SidebarProps> = {}) {
  const props: SidebarProps = {
    items: [item("a"), item("b", { job: { id: "j", key: "auto", startedAt: 0, phase: "queued" } }), item("c", { error: new ApiError("engine_crashed", "The trace crashed (signal 9)") })],
    failed: [],
    selected: "a",
    formats: "PNG, JPG, HEIC, etc.",
    canDownscale: true,
    onAdd: vi.fn(),
    onSelect: vi.fn(),
    onSelectNext: vi.fn(),
    onClear: vi.fn(),
    onDownscale: vi.fn(),
    onDismissFailure: vi.fn(),
    ...patch,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar", () => {
  it("lists every image with its size and status, the selected one marked", () => {
    setup();
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(screen.getByText("a.png")).toBeInTheDocument();
    expect(screen.getAllByText(/1200 × 800/)).toHaveLength(3);
    expect(screen.getByText(/Queued/)).toBeInTheDocument();
    expect(screen.getByText(/· Failed/)).toBeInTheDocument();
    expect(screen.getByTitle("The trace crashed (signal 9)")).toBeInTheDocument();
  });

  it("adds, selects, walks with the arrow keys and clears", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /Add Image/ }));
    fireEvent.click(screen.getByText("b.png"));
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Image list" }), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Image list" }), { key: "ArrowUp" });
    fireEvent.click(screen.getByRole("button", { name: /Clear All/ }));
    expect(props.onAdd).toHaveBeenCalledOnce();
    expect(props.onSelect).toHaveBeenCalledWith("b");
    expect(vi.mocked(props.onSelectNext).mock.calls).toEqual([[1], [-1]]);
    expect(props.onClear).toHaveBeenCalledOnce();
  });

  it("offers Downscale for a file over the cap, only where a path and the Mac app allow it", () => {
    const failed = [
      { name: "huge.png", path: "/p/huge.png", error: new ApiError("too_many_pixels", "Image exceeds the 2048x2048 pixel limit") },
      { name: "junk.bin", path: "/p/junk.bin", error: new ApiError("unsupported_format", "File is not a recognised image") },
    ];
    const props = setup({ items: [], failed, selected: null });
    expect(screen.getAllByRole("button", { name: "Downscale to 2048 px" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Downscale to 2048 px" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    expect(props.onDownscale).toHaveBeenCalledWith(0);
    expect(props.onDismissFailure).toHaveBeenCalledWith(1);
  });

  it("does not offer Downscale in a browser, and Clear All is off with nothing to clear", () => {
    setup({ items: [], failed: [{ name: "huge.png", path: null, error: new ApiError("too_many_pixels", "too big") }], canDownscale: false, selected: null });
    expect(screen.queryByRole("button", { name: "Downscale to 2048 px" })).toBeNull();
    setup({ items: [], failed: [], selected: null });
    expect(screen.getAllByRole("button", { name: /Clear All/ }).at(-1)).toBeDisabled();
  });

  it("keeps the failure cards beside the listbox, which holds only options", () => {
    setup({ failed: [{ name: "huge.png", path: "/p/huge.png", error: new ApiError("too_large", "Too big") }] });
    const group = screen.getByRole("group", { name: /huge\.png could not be opened/ });
    expect(screen.getByRole("listbox", { name: "Image list" })).not.toContainElement(group);
  });
});
