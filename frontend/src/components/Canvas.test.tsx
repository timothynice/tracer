import { fireEvent, render, screen } from "@testing-library/react";

import { Canvas } from "./Canvas";
import { SVG } from "@/test/server";

const props = { sourceUrl: "blob:src", svg: SVG, width: 64, height: 64 };

test("split mode shows source, vector and a keyboard-operable divider", () => {
  localStorage.setItem("studi0trace.view", "split");
  render(<Canvas {...props} />);
  expect(screen.getByAltText("Source raster")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
  const divider = screen.getByRole("separator", { name: /comparison divider/i });
  expect(divider).toHaveAttribute("aria-valuenow", "50");
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(divider).toHaveAttribute("aria-valuenow", "52");
});

test("vector mode hides the source; side mode shows both panes", () => {
  render(<Canvas {...props} />);
  fireEvent.mouseDown(screen.getByRole("tab", { name: /vector/i }));
  expect(screen.queryByAltText("Source raster")).not.toBeInTheDocument();
  fireEvent.mouseDown(screen.getByRole("tab", { name: /side by side/i }));
  expect(screen.getByAltText("Source raster")).toBeInTheDocument();
  expect(screen.getByText("Source")).toBeInTheDocument();
  expect(localStorage.getItem("studi0trace.view")).toBe("side");
});

test("overlay exposes an opacity slider; errors and updating render", () => {
  render(<Canvas {...props} svg={undefined} updating errorMessage="Potrace failed: nope" />);
  fireEvent.mouseDown(screen.getByRole("tab", { name: /overlay/i }));
  expect(screen.getByLabelText("Vector opacity")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Potrace failed");
  expect(screen.getByRole("progressbar")).toBeInTheDocument();
});

test("zoom controls change the percentage", () => {
  render(<Canvas {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Zoom to 100%" }));
  expect(screen.getByRole("button", { name: "Zoom to 100%" })).toHaveTextContent("100%");
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByRole("button", { name: "Zoom to 100%" })).toHaveTextContent("125%");
});

test("split mode leaves the vector side empty until a vector exists", () => {
  localStorage.setItem("studi0trace.view", "split");
  const { rerender } = render(<Canvas {...props} svg={undefined} />);
  // The source is clipped to the left of the divider, so nothing is painted on
  // the right: an unclipped source there would read as a finished vector.
  expect(screen.getByTestId("source-pane")).toHaveStyle({ clipPath: "inset(0 50% 0 0)" });
  rerender(<Canvas {...props} />);
  expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
});

test("a failed request shows a persistent error with a retry, not 'No vector yet'", () => {
  const onRetry = vi.fn();
  render(<Canvas {...props} svg={undefined} errorMessage="Server error (502)" onRetry={onRetry} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Server error (502)");
  expect(screen.queryByText("No vector yet")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(onRetry).toHaveBeenCalled();
});

test("while busy it names the phase instead of claiming there is no vector", () => {
  render(<Canvas {...props} svg={undefined} updating busyLabel="Uploading…" />);
  expect(screen.getByText("Uploading…")).toBeInTheDocument();
  expect(screen.queryByText("No vector yet")).not.toBeInTheDocument();
});

test("the canvas does not swallow clicks on its overlay controls", () => {
  const onRetry = vi.fn();
  render(<Canvas {...props} svg={undefined} errorMessage="Server error (502)" onRetry={onRetry} />);
  const button = screen.getByRole("button", { name: /try again/i });
  // Panning captures the pointer on the viewport, which retargets the click
  // away from anything drawn on top of it unless the drag is declined.
  fireEvent.pointerDown(button, { button: 0, pointerId: 1 });
  const img = screen.getByAltText("Source raster");
  const before = img.style.transform;
  fireEvent.pointerMove(button, { clientX: 120, clientY: 40, pointerId: 1 });
  expect(img.style.transform).toBe(before); // no pan started
  fireEvent.click(button);
  expect(onRetry).toHaveBeenCalled();
});
