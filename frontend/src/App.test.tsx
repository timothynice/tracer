import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

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
