import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  server.events.removeAllListeners();
  localStorage.clear();
});
afterAll(() => server.close());

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

/** The `auto` field of every /vectorize request, in order (null when it was not sent). */
function recordTraces(): (string | null)[] {
  const sent: (string | null)[] = [];
  server.events.on("request:start", ({ request }) => {
    if (!request.url.endsWith("/vectorize")) return;
    const i = sent.push("pending") - 1;
    void request.clone().formData().then((f) => (sent[i] = (f.get("auto") as string | null) ?? null));
  });
  return sent;
}

const row = (id: string) => document.querySelector(`[data-preset="${id}"]`) as HTMLElement;
const vector = () => screen.getByRole("img", { name: "Vector result" }).innerHTML;
function threshold() {
  if (!screen.queryByRole("slider", { name: /threshold/i })) fireEvent.click(screen.getByRole("button", { name: /bitmap/i }));
  return screen.getByRole("slider", { name: /threshold/i });
}

async function dropImage() {
  await waitFor(() => expect(screen.getByText(/Connected/)).toBeInTheDocument());
  const file = new File([new Uint8Array([137, 80, 78, 71])], "logo.png", { type: "image/png" });
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [file] } });
}

test("renders the brand, connects, and shows the empty state", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Studi0Trace")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /upload an image/i })).toBeInTheDocument();
  // The header reports the connection, not the engine roster: engines the app
  // does not show still exist on the API and in the bench.
  await waitFor(() => expect(screen.getByText(/Connected/)).toBeInTheDocument());
  expect(screen.queryByText(/vtracer/i)).not.toBeInTheDocument();
});

test("a new image is traced with Auto, and picking a candidate shows its trace without tracing again", async () => {
  const sent = recordTraces();
  renderApp();
  await dropImage();

  await waitFor(() => expect(within(row("auto")).getByText("Chose Crisp — the cleanest at the same fidelity")).toBeInTheDocument());
  expect(sent).toEqual(["true"]);
  expect(row("auto")).toHaveAttribute("aria-pressed", "true");
  expect(vector()).toContain('data-trace="crisp"');
  // The panel follows Auto's choice, so a control moved next starts from it.
  expect(threshold()).toHaveAttribute("aria-valuenow", "200");

  // Every candidate's trace came back with the Auto run: switching is instant.
  fireEvent.click(row("balanced"));
  await waitFor(() => expect(vector()).toContain('data-trace="balanced"'));
  expect(row("balanced")).toHaveAttribute("aria-pressed", "true");
  expect(within(row("crisp")).getByText("Auto's pick")).toBeInTheDocument();
  fireEvent.click(row("auto"));
  await waitFor(() => expect(vector()).toContain('data-trace="crisp"'));
  await new Promise((r) => setTimeout(r, 400)); // past the debounce: nothing else was asked for
  expect(sent).toEqual(["true"]);

  // A preset Auto never tries is traced as before, with its own parameters.
  fireEvent.click(row("poster"));
  await waitFor(() => expect(sent).toEqual(["true", null]));
  await waitFor(() => expect(vector()).not.toContain("data-trace"));
});

test("moving a control leaves Auto and traces with the panel's values", async () => {
  const sent = recordTraces();
  renderApp();
  await dropImage();
  await waitFor(() => expect(within(row("auto")).getByText(/Chose Crisp/)).toBeInTheDocument());

  fireEvent.keyDown(threshold(), { key: "ArrowLeft" });
  expect(row("auto")).toHaveAttribute("aria-pressed", "false");
  await waitFor(() => expect(sent).toEqual(["true", null]));
  // The candidates' own results stay on their rows for comparison.
  expect(within(row("balanced")).getByText("ΔE 0.54 · 6 shapes")).toBeInTheDocument();
});
