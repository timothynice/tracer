import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll } from "vitest";

import { useHealth } from "./useHealth";
import { API_URL } from "@/lib/api";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("goes straight to ok when the server answers", async () => {
  const { result } = renderHook(() => useHealth());
  expect(result.current.status).toBe("checking");
  await waitFor(() => expect(result.current.status).toBe("ok"));
  expect(result.current.health?.version).toBe("0.2.0");
});

test("reports waking while the server fails, then recovers", async () => {
  let calls = 0;
  server.use(
    http.get(`${API_URL}/health`, () => {
      calls += 1;
      return calls < 3 ? HttpResponse.error() : HttpResponse.json({ status: "ok", version: "0.2.0", engines: [] });
    }),
  );
  const seen: string[] = [];
  const { result } = renderHook(() => {
    const state = useHealth({ retryMs: 20 });
    seen.push(state.status);
    return state;
  });
  await waitFor(() => expect(result.current.status).toBe("ok"));
  expect(result.current.attempts).toBe(2);
  expect(seen).toContain("waking");
});

test("gives up to 'down' after the configured attempts but keeps polling", async () => {
  server.use(http.get(`${API_URL}/health`, () => HttpResponse.error()));
  const { result } = renderHook(() => useHealth({ retryMs: 5, giveUpAfter: 2, slowRetryMs: 5 }));
  await waitFor(() => expect(result.current.status).toBe("down"));
  await waitFor(() => expect(result.current.attempts).toBeGreaterThan(2));
});
