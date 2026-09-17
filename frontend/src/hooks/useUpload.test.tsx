import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll } from "vitest";

import { useUpload, type UploadedImage } from "./useUpload";
import { API_URL } from "@/lib/api";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const file = () => new File([new Uint8Array([137, 80, 78, 71])], "logo.png", { type: "image/png" });

test("uploads, hashes and exposes a preview url", async () => {
  const { result } = renderHook(() => useUpload());
  const out: { value?: UploadedImage | null } = {};
  await act(async () => {
    out.value = await result.current.upload(file());
  });
  expect(out.value?.imageId).toBe("a".repeat(32));
  expect(out.value?.hash).toBeTruthy();
  await waitFor(() => expect(result.current.image?.width).toBe(64));
  expect(result.current.image?.previewUrl).toBeTruthy();
  expect(result.current.uploading).toBe(false);
});

test("surfaces server errors and clears them on clear()", async () => {
  server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ detail: { code: "too_large", message: "big" } }, { status: 400 })));
  const { result } = renderHook(() => useUpload());
  await act(async () => {
    await result.current.upload(file());
  });
  expect(result.current.error?.message).toBe("big");
  expect(result.current.image).toBeNull();
  act(() => result.current.clear());
  expect(result.current.error).toBeNull();
});

test("reupload refreshes the image id", async () => {
  const { result } = renderHook(() => useUpload());
  await act(async () => {
    await result.current.upload(file());
  });
  server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: "b".repeat(32), width: 64, height: 64, format: "PNG" })));
  let id: string | null = null;
  await act(async () => {
    id = await result.current.reupload();
  });
  expect(id).toBe("b".repeat(32));
  await waitFor(() => expect(result.current.image?.imageId).toBe("b".repeat(32)));
});
