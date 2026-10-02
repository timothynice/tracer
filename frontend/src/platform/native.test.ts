import { clearMocks, mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { nativePlatform } from "./native";
import { DEFAULT_SETTINGS } from "./types";

type Handler = (cmd: string, payload: unknown) => unknown;
let calls: { cmd: string; payload: unknown }[] = [];

function ipc(handler: Handler) {
  calls = [];
  mockIPC(
    (cmd, payload) => {
      calls.push({ cmd, payload });
      return handler(cmd, payload);
    },
    { shouldMockEvents: true },
  );
}

beforeEach(() => {
  (window as unknown as { isTauri: boolean }).isTauri = true;
  mockWindows("main");
});
afterEach(() => clearMocks());

const opened = { id: "f".repeat(32), name: "logo.png", path: "/pics/logo.png", width: 64, height: 32, format: "PNG" };

describe("native platform", () => {
  it("opens paths, reads each preview once and keeps refusals with their code", async () => {
    ipc((cmd) => {
      if (cmd === "open_paths") return [{ ok: opened }, { failed: { name: "huge.png", path: "/pics/huge.png", error: { status: 400, body: { detail: { code: "too_many_pixels", message: "too big" } } } } }];
      if (cmd === "read_image") return new Uint8Array([1, 2, 3]).buffer;
      throw new Error(`unexpected ${cmd}`);
    });
    const p = nativePlatform();
    const [ok, failed] = await p.openPaths(["/pics/logo.png", "/pics/huge.png"], { downscale: false });
    if (!("ok" in ok) || !("failed" in failed)) throw new Error("shapes");
    expect(ok.ok).toMatchObject({ ...opened, previewUrl: expect.stringMatching(/^blob:/) });
    expect(failed.failed.error.code).toBe("too_many_pixels");
    expect(calls[0]).toEqual({ cmd: "open_paths", payload: { paths: ["/pics/logo.png", "/pics/huge.png"], downscale: false } });
    await p.openPaths(["/pics/logo.png"]);
    expect(calls.filter((c) => c.cmd === "read_image")).toHaveLength(1);
  });

  it("turns a command error into the UI's ApiError, the 422 list included", async () => {
    ipc((cmd) => {
      if (cmd === "vectorize") throw { status: 422, body: { detail: [{ loc: ["vexel", "detail"], msg: "Input should be a valid number" }] } };
      return null;
    });
    const p = nativePlatform();
    await expect(p.vectorize({ imageId: "i", parameters: { detail: "x" }, auto: false, job: "j1" }, { signal: new AbortController().signal })).rejects.toMatchObject({
      code: "validation_error",
      message: "vexel.detail: Input should be a valid number",
      status: 422,
    });
  });

  it("cancels the job when the signal aborts, and passes its phase on", async () => {
    let finish: (v: unknown) => void = () => {};
    ipc((cmd) => {
      if (cmd === "vectorize") return new Promise((r) => (finish = r));
      if (cmd === "cancel_trace") return true;
      return null;
    });
    const p = nativePlatform();
    const phases: string[] = [];
    const ctl = new AbortController();
    const done = p.vectorize({ imageId: "i", parameters: {}, auto: true, job: "j2" }, { signal: ctl.signal, onPhase: (ph) => phases.push(ph) });
    await new Promise((r) => setTimeout(r, 0));
    await emit("trace-phase", { job: "other", phase: "tracing" });
    await emit("trace-phase", { job: "j2", phase: "tracing" });
    ctl.abort();
    finish({ success: true });
    await done;
    expect(phases).toEqual(["tracing"]);
    expect(calls.find((c) => c.cmd === "cancel_trace")?.payload).toEqual({ job: "j2" });
    expect(calls.find((c) => c.cmd === "vectorize")?.payload).toEqual({ imageId: "i", parameters: {}, auto: true, job: "j2" });
  });

  it("sends an export's bytes and returns the written path", async () => {
    ipc((cmd) => (cmd === "export_file" ? "/pics/logo.svg" : null));
    const bytes = new TextEncoder().encode("<svg/>");
    const path = await nativePlatform().exportFile({ kind: "svg", imageId: "i", name: "logo.svg", bytes }, DEFAULT_SETTINGS);
    expect(path).toBe("/pics/logo.svg");
    expect(calls[0].payload).toEqual(bytes);
  });

  it("hands over paths that arrived before the page listened, then new ones", async () => {
    ipc((cmd) => (cmd === "take_pending_opens" ? ["/early.png"] : null));
    const got: string[][] = [];
    const off = nativePlatform().onOpenPaths((paths) => got.push(paths));
    await new Promise((r) => setTimeout(r, 0));
    await emit("open-paths", ["/later.png"]);
    off();
    await emit("open-paths", ["/after-off.png"]);
    expect(got).toEqual([["/early.png"], ["/later.png"]]);
  });

  it("routes menu items and settings changes", async () => {
    ipc(() => null);
    const p = nativePlatform();
    const menu: string[] = [];
    const settings: unknown[] = [];
    p.onMenu((id) => menu.push(id));
    p.onSettings((s) => settings.push(s));
    await new Promise((r) => setTimeout(r, 0));
    await emit("menu", { id: "zoom-fit" });
    await emit("settings-changed", { ...DEFAULT_SETTINGS, appearance: "dark" });
    expect(menu).toEqual(["zoom-fit"]);
    expect(settings).toEqual([{ ...DEFAULT_SETTINGS, appearance: "dark" }]);
    expect(p.windowRole()).toBe("main");
  });
});
