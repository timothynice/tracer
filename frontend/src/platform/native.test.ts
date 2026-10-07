import { clearMocks, mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

  it("sends Export All its images by id and returns what was written and what was not", async () => {
    const answer = { written: ["/out/a.svg"], failed: [{ id: "b", name: "b.svg", message: "no" }] };
    ipc((cmd) => (cmd === "export_all" ? answer : null));
    const files = [{ id: "a", name: "a.svg", svg: "<svg/>" }, { id: "b", name: "b.svg", svg: "<svg/>" }];
    expect(await nativePlatform().exportAll(files, DEFAULT_SETTINGS)).toEqual(answer);
    expect(calls[0].payload).toEqual({ items: files, reveal: false });
  });

  it("asks the app to confirm Clear All with the count it would lose", async () => {
    ipc((cmd) => {
      if (cmd === "confirm_clear") return false;
      throw new Error(`unexpected ${cmd}`);
    });
    await expect(nativePlatform().confirmClear(3)).resolves.toBe(false);
    expect(calls[0]).toMatchObject({ cmd: "confirm_clear", payload: { unexported: 3 } });
  });

  it("hands the app's open failures over as failures with their code", async () => {
    ipc(() => null);
    const got: unknown[] = [];
    const off = nativePlatform().onOpenFailures((f) => got.push(...f));
    await new Promise((r) => setTimeout(r, 0));
    await emit("open-failures", [{ name: "Dropped items", path: null, error: { status: 400, body: { detail: { code: "nothing_to_open", message: "Nothing to open: drop image files or a folder of them." } } } }]);
    off();
    expect(got).toHaveLength(1);
    expect(got[0]).toMatchObject({ name: "Dropped items", path: null, error: { code: "nothing_to_open", message: "Nothing to open: drop image files or a folder of them." } });
  });

  it("hands over paths that arrived before the page listened, then new ones", async () => {
    // the mock keeps a listener its unlisten should have dropped (it reads `id` where the API sends `eventId`), and
    // the API has already forgotten the callback: the warning is the mock's, nothing is delivered
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    ipc((cmd) => (cmd === "take_pending_opens" ? ["/early.png"] : null));
    const got: string[][] = [];
    const off = nativePlatform().onOpenPaths((paths) => got.push(paths));
    await new Promise((r) => setTimeout(r, 0));
    await emit("open-paths", ["/later.png"]);
    off();
    await emit("open-paths", ["/after-off.png"]);
    expect(got).toEqual([["/early.png"], ["/later.png"]]);
    warn.mockRestore();
  });

  it("keeps early paths for the listener that is there when they are taken, across a remount", async () => {
    let release: (v: string[]) => void = () => {};
    ipc((cmd) => (cmd === "take_pending_opens" ? new Promise<string[]>((r) => (release = r)) : null));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const p = nativePlatform();
    const first: string[][] = [];
    const second: string[][] = [];
    const off1 = p.onOpenPaths((paths) => first.push(paths));
    await new Promise((r) => setTimeout(r, 10));
    off1();
    const off2 = p.onOpenPaths((paths) => second.push(paths));
    await new Promise((r) => setTimeout(r, 10));
    release(["/early.png"]);
    await new Promise((r) => setTimeout(r, 10));
    off2();
    expect(first).toEqual([]);
    expect(second).toEqual([["/early.png"]]);
    expect(calls.filter((c) => c.cmd === "take_pending_opens")).toHaveLength(1);
    warn.mockRestore();
  });

  it("holds early paths that are taken between listeners until the next one subscribes, once", async () => {
    let release: (v: string[]) => void = () => {};
    ipc((cmd) => (cmd === "take_pending_opens" ? new Promise<string[]>((r) => (release = r)) : null));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const p = nativePlatform();
    const off1 = p.onOpenPaths(() => {});
    await new Promise((r) => setTimeout(r, 10));
    off1();
    release(["/early.png"]);
    await new Promise((r) => setTimeout(r, 10));
    const got: string[][] = [];
    const off2 = p.onOpenPaths((paths) => got.push(paths));
    await new Promise((r) => setTimeout(r, 10));
    off2();
    const again: string[][] = [];
    p.onOpenPaths((paths) => again.push(paths))();
    expect(got).toEqual([["/early.png"]]);
    expect(again).toEqual([]);
    expect(calls.filter((c) => c.cmd === "take_pending_opens")).toHaveLength(1);
    warn.mockRestore();
  });

  it("hears a phase the app reports the moment the trace starts", async () => {
    ipc(async (cmd) => {
      if (cmd === "vectorize") {
        await emit("trace-phase", { job: "j3", phase: "tracing" });
        return { success: true };
      }
      return null;
    });
    // a real listen crosses a process boundary and settles later than the next command: make the mock's slow
    const internals = (window as unknown as { __TAURI_INTERNALS__: { invoke: (cmd: string, ...rest: unknown[]) => Promise<unknown> } }).__TAURI_INTERNALS__;
    const fast = internals.invoke;
    internals.invoke = async (cmd, ...rest) => {
      if (cmd === "plugin:event|listen") await new Promise((r) => setTimeout(r, 20));
      return fast(cmd, ...rest);
    };
    const phases: string[] = [];
    await nativePlatform().vectorize({ imageId: "i", parameters: {}, auto: false, job: "j3" }, { signal: new AbortController().signal, onPhase: (ph) => phases.push(ph) });
    expect(phases).toEqual(["tracing"]);
  });

  it("does not start a trace whose signal is already aborted, and leaves no listener behind", async () => {
    ipc(() => null);
    const ctl = new AbortController();
    ctl.abort();
    await expect(nativePlatform().vectorize({ imageId: "i", parameters: {}, auto: false, job: "j4" }, { signal: ctl.signal })).rejects.toMatchObject({ code: "cancelled" });
    expect(calls.filter((c) => c.cmd === "vectorize")).toEqual([]);
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
  it("redraws through the app: this image's phases, the redraw's preview and its drift", async () => {
    const redraw = { ...opened, id: "r".repeat(32), path: null, width: 128, height: 64 };
    let finish: (v: unknown) => void = () => {};
    ipc((cmd) => {
      if (cmd === "redraw_image") return new Promise((r) => (finish = r));
      if (cmd === "read_image") return new Uint8Array([7]).buffer;
      return null;
    });
    const p = nativePlatform();
    const phases: string[] = [];
    const done = p.redrawImage(opened.id, { signal: new AbortController().signal, onPhase: (ph) => phases.push(ph) });
    await new Promise((r) => setTimeout(r, 0));
    await emit("redraw-phase", { id: "other", phase: "drawing" });
    await emit("redraw-phase", { id: opened.id, phase: "uploading" });
    await emit("redraw-phase", { id: opened.id, phase: "drawing" });
    finish({ redraw, drift: { edgeF1: 0.97, deltaE: 1.2, verdict: "close" } });
    const result = await done;
    expect(phases).toEqual(["uploading", "drawing"]);
    expect(result.redraw).toMatchObject({ ...redraw, previewUrl: expect.stringMatching(/^blob:/) });
    expect(result.drift).toEqual({ edgeF1: 0.97, deltaE: 1.2, verdict: "close" });
    expect(calls.find((c) => c.cmd === "redraw_image")?.payload).toEqual({ id: opened.id });
    expect(calls.find((c) => c.cmd === "read_image")?.payload).toEqual({ id: redraw.id });
  });

  it("a cancel abandons the redraw and discards a reply that still arrives", async () => {
    let finish: (v: unknown) => void = () => {};
    ipc((cmd) => (cmd === "redraw_image" ? new Promise((r) => (finish = r)) : true));
    const p = nativePlatform();
    const ctl = new AbortController();
    const done = p.redrawImage("i", { signal: ctl.signal });
    await new Promise((r) => setTimeout(r, 0));
    ctl.abort();
    finish({ redraw: { ...opened, id: "late" }, drift: { edgeF1: 1, deltaE: 0, verdict: "close" } });
    await expect(done).rejects.toMatchObject({ code: "cancelled" });
    expect(calls.find((c) => c.cmd === "cancel_redraw")?.payload).toEqual({ id: "i" });
    expect(calls.find((c) => c.cmd === "discard_redraw")?.payload).toEqual({ id: "i" });
    expect(calls.some((c) => c.cmd === "read_image")).toBe(false);
  });

  it("a cancel that lands while the redraw is being read forgets its preview and discards it", async () => {
    const redraw = { ...opened, id: "r".repeat(32), path: null, width: 128, height: 64 };
    const ctl = new AbortController();
    let release: (v: unknown) => void = () => {};
    ipc((cmd) => {
      if (cmd === "redraw_image") return { redraw, drift: { edgeF1: 1, deltaE: 0, verdict: "close" } };
      if (cmd === "read_image") return new Promise((r) => (release = r));
      return true;
    });
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    const p = nativePlatform();
    const done = p.redrawImage(opened.id, { signal: ctl.signal });
    await vi.waitFor(() => expect(calls.some((c) => c.cmd === "read_image")).toBe(true));
    ctl.abort();
    release(new Uint8Array([7]).buffer);
    await expect(done).rejects.toMatchObject({ code: "cancelled" });
    expect(calls.find((c) => c.cmd === "discard_redraw")?.payload).toEqual({ id: opened.id });
    expect(revoke).toHaveBeenCalledWith(expect.stringMatching(/^blob:/));
    // nothing is left to accept or discard from here
    await p.discardRedraw(opened.id);
    revoke.mockRestore();
  });

  it("Use redraw re-keys the previews: the image shows the redraw, the original keeps its own; Revert swaps back", async () => {
    const redraw = { ...opened, id: "r".repeat(32), path: null, width: 128, height: 64 };
    const original = { ...opened, id: `${opened.id}-original` };
    ipc((cmd) => {
      if (cmd === "open_paths") return [{ ok: opened }];
      if (cmd === "read_image") return new Uint8Array([1]).buffer;
      if (cmd === "redraw_image") return { redraw, drift: { edgeF1: 0.9, deltaE: 3, verdict: "noticeable" } };
      if (cmd === "accept_redraw") return { image: { ...opened, width: 128, height: 64 }, original };
      if (cmd === "revert_redraw") return opened;
      return null;
    });
    let n = 0;
    const create = vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:${(n += 1)}`);
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const p = nativePlatform();
    const [first] = await p.openPaths([opened.path!]);
    if (!("ok" in first)) throw new Error("shape");
    const r = await p.redrawImage(opened.id, { signal: new AbortController().signal });
    const accepted = await p.acceptRedraw(opened.id);
    expect(accepted.image).toMatchObject({ id: opened.id, width: 128, previewUrl: r.redraw.previewUrl });
    expect(accepted.original).toMatchObject({ id: original.id, previewUrl: first.ok.previewUrl });
    const back = await p.revertRedraw(opened.id);
    expect(back).toMatchObject({ id: opened.id, width: 64, previewUrl: first.ok.previewUrl });
    expect(revoke).toHaveBeenCalledWith(r.redraw.previewUrl);
    expect(calls.filter((c) => c.cmd === "read_image")).toHaveLength(2); // the original and the redraw, once each
    create.mockRestore();
    revoke.mockRestore();
  });

  it("keeps the key in the app: status, set and delete are commands, and the key is never read back", async () => {
    ipc((cmd) => (cmd === "redraw_key_status" ? false : null));
    const p = nativePlatform();
    await expect(p.redrawKeyStatus()).resolves.toBe(false);
    await p.setRedrawKey("sk-test-123");
    await p.deleteRedrawKey();
    expect(calls.map((c) => c.cmd)).toEqual(["redraw_key_status", "set_redraw_key", "delete_redraw_key"]);
    expect(calls[1].payload).toEqual({ key: "sk-test-123" });
    const stored: boolean[] = [];
    const off = p.onRedrawKey((s) => stored.push(s));
    await new Promise((r) => setTimeout(r, 0));
    await emit("redraw-key", true);
    off();
    expect(stored).toEqual([true]);
  });

  it("asks the app whether an image looks rough", async () => {
    ipc((cmd) => (cmd === "image_roughness" ? { rough: true, reason: "doubled" } : null));
    await expect(nativePlatform().imageRoughness("i")).resolves.toEqual({ rough: true, reason: "doubled" });
    expect(calls[0]).toEqual({ cmd: "image_roughness", payload: { id: "i" } });
  });
});
