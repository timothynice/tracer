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
