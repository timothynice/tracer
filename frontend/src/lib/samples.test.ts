import { afterEach, expect, test, vi } from "vitest";

import { loadSample } from "./samples";

afterEach(() => vi.restoreAllMocks());

test("a sample is fetched and handed back as a file", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Blob([new Uint8Array([1, 2])], { type: "image/png" })));
  const file = await loadSample("logo.png");
  expect(fetch).toHaveBeenCalledWith("/samples/logo.png");
  expect(file.size).toBe(2);
  expect([file.name, file.type]).toEqual(["logo.png", "image/png"]);
});

test("a sample that is not there is an error", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("missing", { status: 404 }));
  await expect(loadSample("gone.png")).rejects.toThrow(/gone\.png.*404/);
});
