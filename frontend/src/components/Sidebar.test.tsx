import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { paramsKey } from "@/lib/schema";
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
  errorKey: null,
  auto: null,
  ...patch,
});

function setup(patch: Partial<SidebarProps> = {}) {
  const props = props_(patch);
  render(<Sidebar {...props} />);
  return props;
}

function props_(patch: Partial<SidebarProps> = {}): SidebarProps {
  const props: SidebarProps = {
    items: [item("a"), item("b", { job: { id: "j", key: "auto", startedAt: 0, phase: "queued" } }), item("c", { error: new ApiError("engine_crashed", "The trace crashed (signal 9)"), errorKey: "auto" })],
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

  it("badges a traced image, amber when its settings have moved since, and dims one that is tracing", () => {
    const key = paramsKey({ detail: 6 });
    const answer = { svg: "<svg/>", elapsedMs: 1, stats: {} };
    setup({
      items: [
        item("t", { preset: "balanced", params: { detail: 6 }, traces: { [key]: answer }, shown: key }),
        item("s", { preset: "logo", params: { detail: 9 }, traces: { [key]: answer }, shown: key }),
        item("u"),
        item("r", { job: { id: "j", key: "auto", startedAt: 0, phase: "tracing" } }),
      ],
    });
    const badges = screen.getAllByText("SVG");
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveAttribute("title", "Traced");
    expect(badges[1]).toHaveAttribute("title", "Settings changed since this trace");
    expect(badges[1]).toHaveClass("text-warning");
    expect(screen.getByRole("option", { name: /r\.png/ }).querySelector("img")).toHaveClass("opacity-60");
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

  it("says which limit a file went over, not an area", () => {
    const failed = [
      { name: "wide.png", path: "/p/wide.png", error: new ApiError("too_many_pixels", "Image exceeds the 2048x2048 pixel limit") },
      { name: "raised.png", path: "/p/raised.png", error: new ApiError("too_many_pixels", "Image exceeds the 4096x4096 pixel limit") },
      { name: "nomsg.png", path: "/p/nomsg.png", error: new ApiError("too_many_pixels", "") },
      { name: "heavy.png", path: "/p/heavy.png", error: new ApiError("too_large", "File exceeds the 20 MB limit") },
      { name: "heavier.png", path: "/p/heavier.png", error: new ApiError("too_large", "File exceeds the 50 MB limit") },
      { name: "area.png", path: "/p/area.png", error: new ApiError("too_many_pixels", "Image exceeds the 16 megapixel limit") },
      { name: "junk.bin", path: "/p/junk.bin", error: new ApiError("unsupported_format", "File is not a recognised image") },
    ];
    setup({ items: [], failed, selected: null });
    for (const text of [
      "wide.png is larger than 2048 px on a side.",
      "raised.png is larger than 4096 px on a side.",
      "nomsg.png is larger than 2048 px on a side.",
      "heavy.png is larger than 20 MB.",
      "heavier.png is larger than 50 MB.",
      "Image exceeds the 16 megapixel limit",
      "File is not a recognised image",
    ])
      expect(screen.getByText(text)).toBeInTheDocument();
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
  describe("keeps the newest thing in view", () => {
    const scrolled: { id: string; arg: unknown }[] = [];
    const original = Element.prototype.scrollIntoView;
    const install = () => {
      scrolled.length = 0;
      Element.prototype.scrollIntoView = function (this: Element, arg?: boolean | ScrollIntoViewOptions) {
        scrolled.push({ id: this.id || this.getAttribute("aria-label") || "?", arg });
      };
    };
    afterEach(() => {
      Element.prototype.scrollIntoView = original;
    });

    it("scrolls the selected card to the nearest edge when the selection changes", () => {
      install();
      const props = props_({ selected: "a" });
      const { rerender } = render(<Sidebar {...props} />);
      scrolled.length = 0;
      rerender(<Sidebar {...props} selected="c" />);
      expect(scrolled).toEqual([{ id: "image-c", arg: { block: "nearest" } }]);
    });

    it("scrolls a newly opened image into view", () => {
      install();
      const props = props_({ selected: "a" });
      const { rerender } = render(<Sidebar {...props} />);
      scrolled.length = 0;
      rerender(<Sidebar {...props} items={[...props.items, item("d")]} selected="d" />);
      expect(scrolled).toEqual([{ id: "image-d", arg: { block: "nearest" } }]);
    });

    it("scrolls the newest failure card into view, and only when one arrives", () => {
      install();
      const props = props_({ selected: "a" });
      const failure = (name: string) => ({ name, path: `/p/${name}`, error: { code: "unsupported", message: "no" } }) as unknown as SidebarProps["failed"][number];
      const { rerender } = render(<Sidebar {...props} failed={[failure("x.bmp")]} />);
      scrolled.length = 0;
      rerender(<Sidebar {...props} failed={[failure("x.bmp"), failure("y.bmp")]} />);
      expect(scrolled).toEqual([{ id: "y.bmp could not be opened", arg: { block: "nearest" } }]);
      scrolled.length = 0;
      rerender(<Sidebar {...props} failed={[failure("y.bmp")]} />);
      expect(scrolled).toEqual([]);
    });
    it("prefers the new selection when one open brings both an image and a failure", () => {
      install();
      const props = props_({ selected: "a" });
      const { rerender } = render(<Sidebar {...props} />);
      scrolled.length = 0;
      const failed = [{ name: "x.bmp", path: "/p/x.bmp", error: { code: "unsupported", message: "no" } }] as unknown as SidebarProps["failed"];
      rerender(<Sidebar {...props} items={[...props.items, item("d")]} selected="d" failed={failed} />);
      expect(scrolled.at(-1)?.id).toBe("image-d");
    });
  });
});
