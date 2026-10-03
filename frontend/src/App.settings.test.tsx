import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { API_URL } from "@/lib/api";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});
afterAll(() => server.close());

describe("settings in a browser", () => {
  it("open as a sheet from the title bar; Dark applies at once; Trace new images straight away traces on drop", async () => {
    let traces = 0;
    const count = ({ request }: { request: Request }) => {
      if (request.url === `${API_URL}/vectorize`) traces += 1;
    };
    server.events.on("request:start", count);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    fireEvent.click(screen.getByRole("switch", { name: "Trace new images straight away" }));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const dataTransfer = { types: ["Files"], files: [new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" })] };
    fireEvent.dragEnter(document, { dataTransfer });
    fireEvent.drop(document, { dataTransfer });
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(traces).toBe(1);
    expect(JSON.parse(localStorage.getItem("studi0trace.settings")!)).toMatchObject({ appearance: "dark", traceOnOpen: true });
    expect(localStorage.getItem("studi0trace.appearance")).toBe("dark");
    server.events.removeListener("request:start", count);
  });

  it("the keys do nothing to the image behind the sheet while it is open", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    const dataTransfer = { types: ["Files"], files: [new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" })] };
    fireEvent.dragEnter(document, { dataTransfer });
    fireEvent.drop(document, { dataTransfer });
    expect((await screen.findAllByText("logo.png")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    fireEvent.keyDown(dialog, { code: "Backspace", key: "Backspace", metaKey: true });
    fireEvent.keyDown(dialog, { code: "Enter", key: "Enter", metaKey: true });
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getAllByText("logo.png").length).toBeGreaterThan(0);
    expect(screen.queryByRole("img", { name: "Vector result" })).toBeNull();
    // with the sheet closed, the same key removes it
    fireEvent.keyDown(window, { code: "Backspace", key: "Backspace", metaKey: true });
    await screen.findByText("Drop images here");
  });
});
