import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { API_URL } from "@/lib/api";
import { server } from "@/test/server";
import { DEFAULT_SETTINGS } from "./types";
import { webPlatform } from "./web";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

const png = (name = "a.png") => new File([new Uint8Array([137, 80, 78, 71, 1, 2, 3])], name, { type: "image/png" });

describe("web platform", () => {
  it("uploads a file and names it by its hash, not the server's id", async () => {
    server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: "srv-1", width: 64, height: 32, format: "PNG" })));
    const [o] = await webPlatform().openFiles([png()]);
    if (!("ok" in o)) throw new Error("expected ok");
    expect(o.ok).toMatchObject({ name: "a.png", path: null, width: 64, height: 32, format: "PNG" });
    expect(o.ok.id).not.toBe("srv-1");
    expect(o.ok.previewUrl).toMatch(/^blob:/);
  });

  it("reports a refused upload with the server's code and words", async () => {
    server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ detail: { code: "too_many_pixels", message: "Image exceeds the 2048x2048 pixel limit" } }, { status: 400 })));
    const [o] = await webPlatform().openFiles([png()]);
    if (!("failed" in o)) throw new Error("expected a failure");
    expect([o.failed.error.code, o.failed.error.message]).toEqual(["too_many_pixels", "Image exceeds the 2048x2048 pixel limit"]);
  });

  it("traces with the vexel parameters and uploads again when the server has forgotten the image", async () => {
    let uploads = 0;
    const forms: FormData[] = [];
    server.use(
      http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: `srv-${++uploads}`, width: 4, height: 4, format: "PNG" })),
      http.post(`${API_URL}/vectorize`, async ({ request }) => {
        const form = await request.formData();
        forms.push(form);
        if (form.get("image_id") === "srv-1") return HttpResponse.json({ detail: { code: "image_expired", message: "gone" } }, { status: 404 });
        return HttpResponse.json({ success: true, image_id: "srv-2", width: 4, height: 4, results: { vexel: { svg: "<svg/>", elapsed_ms: 1, stats: {} } }, parameters_used: { vexel: {} } });
      }),
    );
    const p = webPlatform();
    const [o] = await p.openFiles([png()]);
    if (!("ok" in o)) throw new Error("expected ok");
    const res = await p.vectorize({ imageId: o.ok.id, parameters: { detail: 7 }, auto: false, job: "j" }, { signal: new AbortController().signal });
    expect(res.results.vexel.svg).toBe("<svg/>");
    expect(uploads).toBe(2);
    expect(forms[1].get("engines")).toBe("vexel");
    expect(JSON.parse(String(forms[1].get("parameters")))).toEqual({ vexel: { detail: 7 } });
  });

  it("keeps settings in localStorage, with no recent list", async () => {
    const p = webPlatform();
    const seen: unknown[] = [];
    p.onSettings((s) => seen.push(s));
    const saved = await p.saveSettings({ ...DEFAULT_SETTINGS, appearance: "dark", recent: ["/x.png"] });
    expect(saved.recent).toEqual([]);
    expect((await webPlatform().loadSettings()).appearance).toBe("dark");
    expect(seen).toHaveLength(1);
  });

  it("has no paths, no menu and no settings window", async () => {
    const p = webPlatform();
    await expect(p.openPaths(["/x.png"])).rejects.toMatchObject({ code: "unsupported" });
    expect(p.openSettingsWindow()).toBe(false);
    expect(p.windowRole()).toBe("main");
  });
});
