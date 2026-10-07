import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

const png = (name = "logo.png") => new File([new Uint8Array([137, 80, 78, 71, 9, 9])], name, { type: "image/png" });

function drop(files: File[]) {
  const dataTransfer = { types: ["Files"], files };
  fireEvent.dragEnter(document, { dataTransfer });
  fireEvent.drop(document, { dataTransfer });
}

describe("App", () => {
  it("opens on an empty window with the title bar and the samples", async () => {
    renderApp();
    expect(await screen.findByText("Drop images here")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Studi0Trace" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Images" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logo" })).toBeInTheDocument();
  });

  it("adds a dropped image to the sidebar, selected, on Auto and untraced", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    const option = await screen.findByRole("option", { name: /logo\.png/ });
    expect(option).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Canvas" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Vector result" })).toBeNull();
    expect(screen.getByRole("radio", { name: /^Auto/ })).toHaveAttribute("aria-checked", "true");
  });

  it("Generate traces with Auto and shows the pick", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    await screen.findByRole("option", { name: /logo\.png/ });
    fireEvent.click(screen.getByRole("button", { name: /Generate Vector/ }));
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "Vectorize" })).getByLabelText(/Auto chose Logo & icon/)).toBeInTheDocument();
  });

  it("picking a candidate after Auto shows its trace at once", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    await screen.findByRole("option", { name: /logo\.png/ });
    fireEvent.click(screen.getByRole("button", { name: /Generate Vector/ }));
    await screen.findByRole("img", { name: "Vector result" });
    fireEvent.click(screen.getByRole("radio", { name: /^Balanced/ }));
    await waitFor(() => expect(document.querySelector('[data-trace="balanced"]')).not.toBeNull());
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
  });

  it("hides and shows the panes from the title bar", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    expect(screen.queryByRole("complementary", { name: "Images" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show inspector" }));
    expect(screen.queryByRole("complementary", { name: "Vectorize" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Images" })).toBeInTheDocument());
  });

  it("says so when the server cannot be reached", async () => {
    const { http, HttpResponse } = await import("msw");
    const { API_URL } = await import("@/lib/api");
    server.use(http.get(`${API_URL}/engines`, () => HttpResponse.error()));
    renderApp();
    expect(await screen.findByText("Studi0Trace could not start")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try Again" })).toBeInTheDocument();
  });
});
