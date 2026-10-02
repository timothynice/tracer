import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

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
    display: { points: false, outlines: false },
    onDisplayChange: vi.fn(),
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
    setup({ mode: "overlay", busy: "Tracing…", errorMessage: "Vexel failed: nope" });
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

  it("split leaves the vector side empty until a vector exists", () => {
    const { props, rerender } = setup({ svg: undefined });
    expect(screen.getByTestId("source-pane")).toHaveStyle({ clipPath: "inset(0 50% 0 0)" });
    rerender(<Viewer {...props} svg={SVG} />);
    expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
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
  });

  it("while busy it names the phase", () => {
    setup({ svg: undefined, busy: "Queued…" });
    expect(screen.getByText("Queued…")).toBeInTheDocument();
  });

  it("the layers button and the display toggles are the caller's", () => {
    const { props } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Layers" }));
    fireEvent.click(screen.getByRole("button", { name: "Show anchor points" }));
    expect(props.onToggleLayers).toHaveBeenCalledOnce();
    expect(props.onDisplayChange).toHaveBeenCalledWith({ points: true });
  });
});
