import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import type { UploadedImage } from "./useUpload";
import { useVectorize } from "./useVectorize";
import { API_URL } from "@/lib/api";
import { server, SVG } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const image: UploadedImage = {
  file: new File([new Uint8Array(4)], "a.png", { type: "image/png" }),
  hash: "h1",
  imageId: "a".repeat(32),
  width: 64,
  height: 64,
  format: "PNG",
  previewUrl: "blob:x",
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

test("traces after the debounce and exposes results", async () => {
  const { result } = renderHook(
    () => useVectorize({ image, engines: ["potrace"], params: { potrace: { threshold: 128 } }, reupload: async () => null, debounceMs: 10 }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe(SVG));
  expect(result.current.updating).toBe(false);
  expect(result.current.data?.parameters_used.potrace.threshold).toBe(128);
});

test("rapid parameter changes collapse into one request and keep previous results visible", async () => {
  let calls = 0;
  server.use(
    http.post(`${API_URL}/vectorize`, async ({ request }) => {
      calls += 1;
      const form = await request.formData();
      const params = JSON.parse(String(form.get("parameters")));
      await new Promise((r) => setTimeout(r, 30));
      return HttpResponse.json({
        success: true, image_id: form.get("image_id"), width: 64, height: 64, parameters_used: params,
        results: { potrace: { svg: `<svg data-t="${params.potrace.threshold}"/>`, elapsed_ms: 1, stats: { paths: 1, nodes: 1, bytes: 1, gradients: 0, unique_fills: 1 } } },
      });
    }),
  );
  let threshold = 100;
  const { result, rerender } = renderHook(
    () => useVectorize({ image, engines: ["potrace"], params: { potrace: { threshold } }, reupload: async () => null, debounceMs: 40 }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.results?.potrace.svg).toContain('data-t="100"'));
  expect(calls).toBe(1);

  for (const t of [110, 120, 130]) {
    threshold = t;
    rerender();
    await new Promise((r) => setTimeout(r, 10));
  }
  expect(result.current.updating).toBe(true);
  expect(result.current.results?.potrace.svg).toContain('data-t="100"'); // previous result stays visible
  await waitFor(() => expect(result.current.results?.potrace.svg).toContain('data-t="130"'));
  expect(calls).toBe(2);
});

test("re-uploads once when the server reports image_expired", async () => {
  let attempt = 0;
  server.use(
    http.post(`${API_URL}/vectorize`, async ({ request }) => {
      attempt += 1;
      const form = await request.formData();
      if (form.get("image_id") === image.imageId) return HttpResponse.json({ detail: { code: "image_expired", message: "gone" } }, { status: 404 });
      return HttpResponse.json({
        success: true, image_id: form.get("image_id"), width: 64, height: 64, parameters_used: {},
        results: { potrace: { svg: SVG, elapsed_ms: 1, stats: { paths: 1, nodes: 1, bytes: 1, gradients: 0, unique_fills: 1 } } },
      });
    }),
  );
  const reupload = vi.fn(async () => "b".repeat(32));
  const { result } = renderHook(() => useVectorize({ image, engines: ["potrace"], params: {}, reupload, debounceMs: 5 }), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe(SVG));
  expect(reupload).toHaveBeenCalledTimes(1);
  expect(attempt).toBe(2);
  expect(result.current.data?.image_id).toBe("b".repeat(32));
});

test("a new image drops the previous image's result instead of showing it", async () => {
  let current = image;
  const { result, rerender } = renderHook(
    () => useVectorize({ image: current, engines: ["potrace"], params: {}, reupload: async () => null, debounceMs: 5 }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe(SVG));

  server.use(
    http.post(`${API_URL}/vectorize`, async () => {
      await new Promise((r) => setTimeout(r, 60));
      return HttpResponse.json({
        success: true, image_id: "c".repeat(32), width: 64, height: 64, parameters_used: {},
        results: { potrace: { svg: "<svg data-second/>", elapsed_ms: 1, stats: { paths: 1, nodes: 1, bytes: 1, gradients: 0, unique_fills: 1 } } },
      });
    }),
  );
  current = { ...image, hash: "h2", imageId: "c".repeat(32) };
  rerender();
  // keepPreviousData is for parameter tweaks; across images the old vector
  // belongs to a different picture and must not be shown.
  expect(result.current.results).toBeUndefined();
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe("<svg data-second/>"));
});

test("refetch() recovers after a failed request", async () => {
  let calls = 0;
  server.use(
    http.post(`${API_URL}/vectorize`, async () => {
      calls += 1;
      if (calls === 1) return new HttpResponse("", { status: 502, statusText: "Bad Gateway" });
      return HttpResponse.json({
        success: true, image_id: image.imageId, width: 64, height: 64, parameters_used: {},
        results: { potrace: { svg: SVG, elapsed_ms: 1, stats: { paths: 1, nodes: 1, bytes: 1, gradients: 0, unique_fills: 1 } } },
      });
    }),
  );
  const { result } = renderHook(() => useVectorize({ image, engines: ["potrace"], params: {}, reupload: async () => null, debounceMs: 5 }), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.error).toBeTruthy());
  expect(result.current.results).toBeUndefined();
  result.current.refetch();
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe(SVG));
  expect(result.current.error).toBeNull();
});

test("auto asks the server to pick, sends no parameters, and ignores the panel's values", async () => {
  const forms: FormData[] = [];
  server.events.on("request:start", ({ request }) => {
    if (request.url.endsWith("/vectorize")) void request.clone().formData().then((f) => forms.push(f));
  });
  let threshold = 100;
  const { result, rerender } = renderHook(
    () => useVectorize({ image, engines: ["potrace"], params: { potrace: { threshold } }, auto: true, reupload: async () => null, debounceMs: 10 }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.data?.auto?.potrace.pick).toBe("crisp"));
  threshold = 150; // what the panel holds plays no part in an Auto trace
  rerender();
  await new Promise((r) => setTimeout(r, 40));
  expect(result.current.updating).toBe(false);
  expect(forms).toHaveLength(1);
  expect(forms[0].get("auto")).toBe("true");
  expect(JSON.parse(String(forms[0].get("parameters")))).toEqual({});
  server.events.removeAllListeners();
});

test("a seeded answer is shown at once for its parameters, with no request and no debounce", async () => {
  let calls = 0;
  server.events.on("request:start", ({ request }) => void (request.url.endsWith("/vectorize") && (calls += 1)));
  let threshold = 128;
  const { result, rerender } = renderHook(
    () => useVectorize({ image, engines: ["potrace"], params: { potrace: { threshold } }, reupload: async () => null, debounceMs: 10_000 }),
    { wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.results?.potrace.svg).toBe(SVG)); // the first trace is not debounced
  expect(calls).toBe(1);
  const seeded = { success: true, image_id: image.imageId, width: 64, height: 64, parameters_used: {}, results: { potrace: { svg: "<svg data-seeded/>" } } };
  result.current.seed({ potrace: { threshold: 200 } }, seeded);
  threshold = 200;
  rerender();
  // No debounce to wait out, and nothing more asked of the server.
  expect(result.current.results?.potrace.svg).toBe("<svg data-seeded/>");
  expect(result.current.updating).toBe(false);
  await new Promise((r) => setTimeout(r, 30));
  expect(calls).toBe(1);
  server.events.removeAllListeners();
});
