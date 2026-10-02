import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

async function withImage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );
  await screen.findByText("Drop images here");
  const dataTransfer = { types: ["Files"], files: [new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" })] };
  fireEvent.dragEnter(document, { dataTransfer });
  fireEvent.drop(document, { dataTransfer });
  await screen.findByRole("option", { name: /logo\.png/ });
}

const press = (code: string, mods: Partial<Record<"shiftKey" | "altKey" | "ctrlKey", boolean>> = {}) => fireEvent.keyDown(window, { code, metaKey: true, ...mods });

describe("commands in a browser", () => {
  it("⌘↩ traces, ⌘2 compares side by side, ⌃⌘S hides the sidebar, ⌘⌫ removes the image", async () => {
    await withImage();
    press("Enter");
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    press("Digit2");
    expect(screen.getByRole("tab", { name: "Side by side" })).toHaveAttribute("aria-selected", "true");
    press("KeyS", { ctrlKey: true });
    expect(screen.queryByRole("complementary", { name: "Images" })).toBeNull();
    press("KeyS", { ctrlKey: true });
    press("Backspace");
    await waitFor(() => expect(screen.queryByRole("option", { name: /logo\.png/ })).toBeNull());
    expect(screen.getByText("Drop images here")).toBeInTheDocument();
  });

  it("a right click on an image offers Remove", async () => {
    await withImage();
    fireEvent.contextMenu(screen.getByRole("option", { name: /logo\.png/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByRole("option", { name: /logo\.png/ })).toBeNull());
  });

  it("⌘⌫ held down (a repeat) removes nothing more, and in a text field it is left to the field", async () => {
    await withImage();
    fireEvent.keyDown(window, { code: "Backspace", metaKey: true, repeat: true });
    expect(screen.getByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Advanced Options" }));
    fireEvent.click(screen.getByRole("button", { name: /Shapes/ }));
    const field = screen.getByRole("spinbutton", { name: "Smallest shape" });
    field.focus();
    fireEvent.keyDown(field, { code: "Backspace", metaKey: true });
    expect(screen.getByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
  });
});
