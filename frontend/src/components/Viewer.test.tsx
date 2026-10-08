import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SVG } from "@/test/server";
import { Viewer, type ViewerHandle, type ViewerProps } from "./Viewer";

function setup(patch: Partial<ViewerProps> = {}) {
  const ref = createRef<ViewerHandle>();
  const props: ViewerProps = {
    sourceUrl: "blob:src",
    svg: SVG,
    width: 64,
    height: 64,
    mode: "split",
    onModeChange: vi.fn(),
    busy: null,
    layersOpen: false,
    onToggleLayers: vi.fn(),
    ...patch,
  };
  const utils = render(<Viewer ref={ref} {...props} />);
  return { ...utils, ref, props };
}

const zoomLevel = () => screen.getByRole("button", { name: "Zoom level" });
const viewport = () => screen.getByTestId("viewport");

describe("Viewer", () => {
  it("split shows the source and the vector, labels both, and has a keyboard divider", () => {
    setup();
    expect(screen.getByAltText("Source raster")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("Vector")).toBeInTheDocument();
    const divider = screen.getByRole("separator", { name: /comparison divider/i });
    expect(divider).toHaveAttribute("aria-valuenow", "50");
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(divider).toHaveAttribute("aria-valuenow", "52");
  });

  it("before a trace the source shows whole and ghosted: no clip, no divider, no labels", () => {
    const { props, rerender } = setup({ svg: undefined });
    const pane = screen.getByTestId("source-pane");
    expect(pane).toHaveClass("ghost");
    expect(pane.style.clipPath).toBe("");
    expect(screen.queryByRole("separator", { name: /comparison divider/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Original")).toBeNull();
    expect(screen.queryByText("Vector")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate Vector" })).toBeInTheDocument();
    rerender(<Viewer {...props} svg={SVG} />);
    expect(screen.queryByRole("button", { name: "Generate Vector" })).toBeNull();
    expect(screen.getByTestId("source-pane")).not.toHaveClass("ghost");
    expect(screen.getByRole("separator", { name: /comparison divider/i })).toBeInTheDocument();
    expect(screen.getByText("Original")).toBeInTheDocument();
  });

  it("the canvas's Generate button traces, and its click is not swallowed by panning", () => {
    const onGenerate = vi.fn();
    setup({ svg: undefined, onGenerate });
    const button = screen.getByRole("button", { name: "Generate Vector" });
    fireEvent.pointerDown(button, { button: 0, pointerId: 1 });
    const before = screen.getByAltText("Source raster").style.transform;
    fireEvent.pointerMove(button, { clientX: 120, clientY: 40, pointerId: 1 });
    expect(screen.getByAltText("Source raster").style.transform).toBe(before);
    fireEvent.click(button);
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("side by side before a trace is the same ghost, not two panes", () => {
    setup({ svg: undefined, mode: "side" });
    expect(screen.getByTestId("source-pane")).toHaveClass("ghost");
    expect(screen.queryByText("Original")).toBeNull();
  });

  it("the mode is the caller's: the tabs ask for a change, the prop decides", () => {
    const { props, rerender } = setup();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Vector" }));
    expect(props.onModeChange).toHaveBeenCalledWith("vector");
    rerender(<Viewer {...props} mode="vector" />);
    expect(screen.queryByAltText("Source raster")).toBeNull();
    rerender(<Viewer {...props} mode="side" />);
    expect(screen.getByAltText("Source raster")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Side by side" })).toHaveAttribute("aria-selected", "true");
  });

  it("overlay has an opacity slider; errors and the busy bar show", () => {
    setup({ mode: "overlay", busy: { phase: "Tracing…", startedAt: 0 }, errorMessage: "Vexel failed: nope" });
    expect(screen.getByLabelText("Vector opacity")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Vexel failed");
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("the handle zooms: actual size, in, out", () => {
    const { ref } = setup();
    act(() => ref.current!.actualSize());
    expect(zoomLevel()).toHaveTextContent("100%");
    act(() => ref.current!.zoomIn());
    expect(zoomLevel()).toHaveTextContent("125%");
    act(() => ref.current!.zoomOut());
    act(() => ref.current!.zoomOut());
    expect(zoomLevel()).toHaveTextContent("80%");
  });

  it("two-finger scroll pans and a pinch zooms", () => {
    setup();
    const img = screen.getByAltText("Source raster");
    const before = img.style.transform;
    act(() => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaX: 30, deltaY: 10, bubbles: true, cancelable: true }));
    });
    expect(img.style.transform).not.toBe(before);
    expect(zoomLevel()).toHaveTextContent("100%");
    act(() => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaY: -50, ctrlKey: true, bubbles: true, cancelable: true }));
    });
    expect(zoomLevel()).not.toHaveTextContent("100%");
  });

  it("the zoom tool zooms in where clicked, and out with Option", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Zoom tool" }));
    fireEvent.pointerDown(viewport(), { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    expect(zoomLevel()).toHaveTextContent("200%");
    fireEvent.pointerDown(viewport(), { button: 0, pointerId: 1, clientX: 10, clientY: 10, altKey: true });
    expect(zoomLevel()).toHaveTextContent("100%");
  });

  it("while tracing the ghost stays, a band sweeps it and the pill counts the seconds", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      setup({ svg: undefined, busy: { phase: "Tracing…", startedAt: Date.now() } });
      expect(screen.getByTestId("source-pane")).toHaveClass("ghost");
      expect(screen.getByTestId("sweep")).toBeInTheDocument();
      expect(screen.getByText("Tracing… 0 s")).toBeInTheDocument();
      act(() => vi.advanceTimersByTime(4000));
      expect(screen.getByText("Tracing… 4 s")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a queued job says so without counting", () => {
    setup({ svg: undefined, busy: { phase: "Queued…", startedAt: Date.now() } });
    expect(screen.getByText("Queued…")).toBeInTheDocument();
  });

  it("a re-trace over a vector dims it under the progress bar, with no sweep", () => {
    setup({ busy: { phase: "Tracing…", startedAt: Date.now() } });
    expect(screen.getByRole("progressbar", { name: "Tracing" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Vector result" })).toHaveClass("opacity-60");
    expect(screen.queryByTestId("sweep")).toBeNull();
  });

  it("a failure is a persistent error with Try Again, and its click is not swallowed by panning", () => {
    const onRetry = vi.fn();
    setup({ svg: undefined, errorMessage: "The trace crashed (signal 9)", onRetry });
    const button = screen.getByRole("button", { name: /try again/i });
    fireEvent.pointerDown(button, { button: 0, pointerId: 1 });
    const img = screen.getByAltText("Source raster");
    const before = img.style.transform;
    fireEvent.pointerMove(button, { clientX: 120, clientY: 40, pointerId: 1 });
    expect(img.style.transform).toBe(before);
    fireEvent.click(button);
    expect(onRetry).toHaveBeenCalled();
    expect(screen.queryByText("No vector yet")).toBeNull();
    expect(screen.getByTestId("source-pane")).toHaveClass("ghost");
  });

  it("the layers button is the caller's", () => {
    const { props } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Layers" }));
    expect(props.onToggleLayers).toHaveBeenCalledOnce();
  });

  it("wheel and gesture events over the panel are the panel's: no pan, no preventDefault", () => {
    setup({ panel: <div data-overlay-ui data-testid="p"><span data-testid="row">layer</span></div> });
    const img = screen.getByAltText("Source raster");
    const before = img.style.transform;
    const row = screen.getByTestId("row");
    const wheel = new WheelEvent("wheel", { deltaY: 40, bubbles: true, cancelable: true });
    act(() => {
      row.dispatchEvent(wheel);
    });
    expect(wheel.defaultPrevented).toBe(false);
    expect(img.style.transform).toBe(before);
    const pinch = new WheelEvent("wheel", { deltaY: -40, ctrlKey: true, bubbles: true, cancelable: true });
    act(() => {
      row.dispatchEvent(pinch);
    });
    expect(pinch.defaultPrevented).toBe(false);
    expect(zoomLevel()).toHaveTextContent("100%");
    const gesture = new Event("gesturestart", { bubbles: true, cancelable: true });
    act(() => {
      row.dispatchEvent(gesture);
    });
    expect(gesture.defaultPrevented).toBe(false);
    // and over the image the same wheel is the viewer's
    const own = new WheelEvent("wheel", { deltaY: 40, bubbles: true, cancelable: true });
    act(() => {
      viewport().dispatchEvent(own);
    });
    expect(own.defaultPrevented).toBe(true);
  });

  it("the hand lets go of Space when the window loses focus", () => {
    setup();
    fireEvent.keyDown(window, { code: "Space" });
    expect(viewport().className).toContain("cursor-grab");
    fireEvent.click(screen.getByRole("button", { name: "Zoom tool" }));
    expect(viewport().className).toContain("cursor-grab");
    fireEvent.blur(window);
    expect(viewport().className).toContain("cursor-zoom-in");
    fireEvent.keyDown(window, { code: "Space", metaKey: true });
    expect(viewport().className).toContain("cursor-zoom-in");
  });
});

describe("Viewer refit on resize", () => {
  const size = { w: 464, h: 464 };
  let resized: ((rect: { width: number; height: number }) => void) | null = null;

  function mount() {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: (entries: { contentRect: { width: number; height: number } }[]) => void) {
          resized = (rect) => cb([{ contentRect: rect }]);
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => size.w);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => size.h);
    return setup();
  }
  const resize = (side: number) => {
    size.w = size.h = side;
    act(() => resized!({ width: side, height: side }));
  };
  // a 64 px image with 32 px of padding, fitted above the toolbar (56 px + 8 px of air below, 40 px above for the chips), centred in what is left
  const fitted = (side: number) => {
    const availH = side - 40 - 64;
    const scale = Math.min((side - 64) / 64, availH / 64);
    return `translate(${(side - 64 * scale) / 2}px, ${40 + (availH - 64 * scale) / 2}px) scale(${scale})`;
  };

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    size.w = size.h = 464;
  });

  it("an untouched view refits when the pane resizes", () => {
    mount();
    const img = screen.getByAltText("Source raster");
    expect(img.style.transform).toBe(fitted(464));
    resize(264);
    expect(img.style.transform).toBe(fitted(264));
  });

  it("a fit leaves the toolbar's space: the image's bottom edge clears the toolbar's top", () => {
    mount();
    const img = screen.getByAltText("Source raster");
    const m = /translate\(([\d.]+)px, ([\d.]+)px\) scale\(([\d.]+)\)/.exec(img.style.transform)!;
    const bottom = Number(m[2]) + 64 * Number(m[3]);
    expect(bottom).toBeLessThanOrEqual(size.h - 12 - 44);
    expect(Number(m[2])).toBeGreaterThanOrEqual(40);
  });

  it("a panned or zoomed view stays where the user left it", () => {
    mount();
    const img = screen.getByAltText("Source raster");
    act(() => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaX: 30, deltaY: 10, bubbles: true, cancelable: true }));
    });
    const panned = img.style.transform;
    expect(panned).not.toBe(fitted(464));
    resize(264);
    expect(img.style.transform).toBe(panned);
  });

  it("fit() re-arms refitting", () => {
    const { ref } = mount();
    const img = screen.getByAltText("Source raster");
    act(() => ref.current!.zoomIn());
    resize(264);
    expect(img.style.transform).not.toBe(fitted(264));
    act(() => ref.current!.fit());
    expect(img.style.transform).toBe(fitted(264));
    resize(364);
    expect(img.style.transform).toBe(fitted(364));
  });
  it("fits a tiny image at no more than the zoom cap, so Zoom In never zooms out", () => {
    const size = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1000);
    const height = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(800);
    try {
      const { ref } = setup({ width: 16, height: 16 });
      expect(parseInt(zoomLevel().textContent!)).toBe(3200);
      act(() => ref.current!.zoomIn());
      expect(parseInt(zoomLevel().textContent!)).toBe(3200);
      act(() => ref.current!.zoomOut());
      expect(parseInt(zoomLevel().textContent!)).toBeLessThan(3200);
    } finally {
      size.mockRestore();
      height.mockRestore();
    }
  });

  it("the divider states its range for assistive technology", () => {
    setup();
    const divider = screen.getByRole("separator", { name: /comparison divider/i });
    expect(divider).toHaveAttribute("aria-valuemin", "0");
    expect(divider).toHaveAttribute("aria-valuemax", "100");
    expect(divider).toHaveAttribute("aria-valuenow", "50");
  });

  it("compares two rasters: the redraw takes the vector's place, under its own label", () => {
    const { props, rerender } = setup({ svg: undefined, compare: { url: "blob:redraw", label: "AI redraw" } });
    expect(screen.getByAltText("Source raster")).toHaveAttribute("src", "blob:src");
    expect(screen.getByAltText("AI redraw")).toHaveAttribute("src", "blob:redraw");
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("AI redraw")).toBeInTheDocument();
    expect(screen.queryByText("Vector")).toBeNull();
    expect(screen.getByRole("separator", { name: /comparison divider/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate Vector" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Layers" })).toBeNull();
    rerender(<Viewer {...props} mode="side" />);
    expect(screen.getByText("AI redraw")).toBeInTheDocument();
    expect(screen.getByAltText("AI redraw")).toBeInTheDocument();
  });

  it("names the right-hand tab and the overlay slider for the redraw, not the vector", () => {
    const { props, rerender } = setup({ svg: undefined, compare: { url: "blob:redraw", label: "AI redraw" } });
    expect(screen.getByRole("tab", { name: "AI redraw" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Vector" })).toBeNull();
    rerender(<Viewer {...props} mode="overlay" />);
    expect(screen.getByLabelText("AI redraw opacity")).toBeInTheDocument();
    expect(screen.queryByLabelText("Vector opacity")).toBeNull();
  });
});
