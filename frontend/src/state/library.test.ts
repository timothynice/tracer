import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type EngineDescription, type Preset, type VectorizeResponse } from "@/lib/api";
import { paramsKey } from "@/lib/schema";
import type { OpenOutcome, Phase, Platform, TraceRequest } from "@/platform/types";
import { DEFAULT_SETTINGS } from "@/platform/types";
import { createLibrary, DEBOUNCE_MS, needsUpdate, traceKey, unexportedCount, type Catalog } from "./library";

const ENGINE: EngineDescription = {
  id: "vexel",
  label: "Vexel",
  description: "",
  primary: true,
  params: {
    properties: {
      detail: { type: "number", default: 6, minimum: 1, maximum: 20 },
      min_region: { type: "integer", default: 8, minimum: 1, maximum: 64 },
    },
  },
  defaults: { detail: 6, min_region: 8 },
};
const PRESETS: Preset[] = [
  { id: "auto", label: "Auto", engine: "vexel", description: "", detail: "", sample: "auto.png", params: {}, kind: "auto" },
  { id: "balanced", label: "Balanced", engine: "vexel", description: "", detail: "", sample: "b.png", params: {}, auto_candidate: true },
  { id: "logo", label: "Logo & icon", engine: "vexel", description: "", detail: "", sample: "l.png", params: { detail: 10, min_region: 16 }, auto_candidate: true },
];
const CATALOG: Catalog = { engine: ENGINE, presets: PRESETS };

const image = (id: string, path: string | null = `/pics/${id}.png`) => ({ id, name: `${id}.png`, path, width: 64, height: 32, format: "PNG", previewUrl: `blob:${id}` });
const ok = (id: string, path?: string | null): OpenOutcome => ({ ok: image(id, path) });

function response(svg: string, elapsed: number, extra: Partial<VectorizeResponse> = {}): VectorizeResponse {
  return {
    success: true,
    image_id: "x",
    width: 64,
    height: 32,
    results: { vexel: { svg, elapsed_ms: elapsed, stats: { paths: 3 } } },
    parameters_used: { vexel: {} },
    auto: null,
    ...extra,
  };
}

interface Call { req: TraceRequest; signal: AbortSignal; onPhase?: (p: Phase) => void; resolve: (r: VectorizeResponse) => void; reject: (e: unknown) => void }

function fakePlatform() {
  const calls: Call[] = [];
  const platform = {
    vectorize: vi.fn((req: TraceRequest, opts: { signal: AbortSignal; onPhase?: (p: Phase) => void }) =>
      new Promise<VectorizeResponse>((resolve, reject) => calls.push({ req, signal: opts.signal, onPhase: opts.onPhase, resolve, reject })),
    ),
    closeImage: vi.fn(),
    openPaths: vi.fn(async (paths: string[]) => paths.map((p) => ok("small", p))),
  } satisfies Pick<Platform, "vectorize" | "closeImage" | "openPaths">;
  return { platform, calls };
}

// the timers are fake; a few turns of the microtask queue let a settled promise's handlers run
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

describe("library", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }));
  afterEach(() => vi.useRealTimers());

  it("opens images on Auto with the engine's defaults, selects the last, and does not trace", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    const s = lib.getState();
    expect(s.items.map((i) => i.image.id)).toEqual(["a", "b"]);
    expect(s.selected).toBe("b");
    expect(s.items[0]).toMatchObject({ preset: "auto", params: { detail: 6, min_region: 8 }, shown: null, job: null });
    expect(platform.vectorize).not.toHaveBeenCalled();
  });

  it("keeps one card per file and keeps the files it could not open", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    const failure = { failed: { name: "huge.png", path: "/pics/huge.png", error: new ApiError("too_many_pixels", "Image exceeds the 2048x2048 pixel limit", 400) } };
    lib.add([ok("a"), ok("a"), failure]);
    expect(lib.getState().items).toHaveLength(1);
    expect(lib.getState().failed.map((f) => f.name)).toEqual(["huge.png"]);
  });

  it("traces Auto, keeps every candidate as its preset's trace, and follows the pick", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.generate("a");
    expect(calls[0].req).toMatchObject({ imageId: "a", auto: true, parameters: {} });
    expect(lib.getState().items[0].job).toMatchObject({ key: "auto", phase: "queued" });
    calls[0].onPhase?.("tracing");
    expect(lib.getState().items[0].job?.phase).toBe("tracing");
    calls[0].resolve(
      response("<svg>logo</svg>", 900, {
        auto: {
          vexel: {
            engine: "vexel",
            pick: "logo",
            reason: "the cleanest at the same fidelity",
            candidates: [
              { preset: "balanced", label: "Balanced", svg: "<svg>balanced</svg>", elapsed_ms: 800, stats: { paths: 9 } },
              { preset: "logo", label: "Logo & icon", svg: "<svg>logo</svg>", elapsed_ms: 900, stats: { paths: 4 } },
            ],
          },
        },
      }),
    );
    await flush();
    const item = lib.getState().items[0];
    expect(item.shown).toBe("auto");
    expect(item.job).toBeNull();
    expect(item.auto?.pick).toBe("logo");
    expect(item.params).toEqual({ detail: 10, min_region: 16 });
    expect(item.traces[paramsKey({ detail: 6, min_region: 8 })].svg).toBe("<svg>balanced</svg>");
    // a candidate is shown at once, nothing traced again
    lib.pickPreset("a", PRESETS[1]);
    expect(lib.getState().items[0].shown).toBe(paramsKey({ detail: 6, min_region: 8 }));
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
  });

  it("re-traces by itself after a quick trace, and asks for Update after a slow one", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    calls[0].resolve(response("<svg>a</svg>", 500));
    await flush();
    lib.setParam("a", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS - 1);
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(platform.vectorize).toHaveBeenCalledTimes(2);
    expect(calls[1].req.parameters).toEqual({ detail: 7, min_region: 8 });

    lib.pickPreset("b", PRESETS[1]);
    lib.generate("b");
    calls[2].resolve(response("<svg>b</svg>", 5000));
    await flush();
    lib.setParam("b", "detail", 9);
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(3);
    expect(needsUpdate(lib.getState().items[1])).toBe(true);
  });

  it("does not re-trace by itself when live updates are off", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, { ...DEFAULT_SETTINGS, liveUpdate: false });
    lib.add([ok("a")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    calls[0].resolve(response("<svg>a</svg>", 100));
    await flush();
    lib.setParam("a", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
  });

  it("a newer request supersedes the running one, whose late answer is dropped", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    lib.setParam("a", "detail", 12);
    lib.generate("a");
    expect(calls[0].signal.aborted).toBe(true);
    calls[1].resolve(response("<svg>new</svg>", 100));
    calls[0].resolve(response("<svg>old</svg>", 100));
    await flush();
    const item = lib.getState().items[0];
    expect(item.traces[traceKey(item)].svg).toBe("<svg>new</svg>");
    expect(Object.values(item.traces).map((t) => t.svg)).not.toContain("<svg>old</svg>");
  });

  it("cancel drops the job at once and is not an error", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.generate("a");
    lib.cancel("a");
    expect(lib.getState().items[0].job).toBeNull();
    expect(calls[0].signal.aborted).toBe(true);
    calls[0].reject(new ApiError("cancelled", "Cancelled", 499));
    await flush();
    expect(lib.getState().items[0].error).toBeNull();
  });

  it("an engine's error and a failed request become the item's error", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.generate("a");
    calls[0].resolve({ ...response("", 0), results: { vexel: { error: { code: "engine_crashed", message: "The trace crashed" } } } });
    lib.generate("b");
    calls[1].reject(new ApiError("engine_crashed", "The trace crashed", 500));
    await flush();
    expect(lib.getState().items.map((i) => i.error?.code)).toEqual(["engine_crashed", "engine_crashed"]);
    expect(lib.getState().items.map((i) => i.job)).toEqual([null, null]);
  });

  it("remove closes the image, cancels its job and moves the selection", () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b"), ok("c")]);
    lib.select("b");
    lib.generate("b");
    lib.remove("b");
    expect(platform.closeImage).toHaveBeenCalledWith("b");
    expect(calls[0].signal.aborted).toBe(true);
    expect(lib.getState().selected).toBe("c");
    lib.remove("c");
    expect(lib.getState().selected).toBe("a");
    lib.clear();
    expect(lib.getState()).toEqual({ items: [], failed: [], selected: null });
  });

  it("selectNext walks the list and stops at the ends", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.selectNext(1);
    expect(lib.getState().selected).toBe("b");
    lib.selectNext(-1);
    lib.selectNext(-1);
    expect(lib.getState().selected).toBe("a");
  });

  it("downscale reopens a too-large file at 2048 px in its place", async () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([{ failed: { name: "huge.png", path: "/pics/huge.png", error: new ApiError("too_many_pixels", "too big", 400) } }]);
    await lib.downscale(0);
    expect(platform.openPaths).toHaveBeenCalledWith(["/pics/huge.png"], { downscale: true });
    expect(lib.getState().failed).toEqual([]);
    expect(lib.getState().items.map((i) => i.image.id)).toEqual(["small"]);
  });

  it("traces new images straight away when the setting says so", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, { ...DEFAULT_SETTINGS, traceOnOpen: true });
    lib.add([ok("a"), ok("b")]);
    expect(platform.vectorize).toHaveBeenCalledTimes(2);
  });

  it("a trace landing after the settings moved on does not take the screen or the controls", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    // A: the job for detail 7 is running when the user goes back to the cached detail 6
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    calls[0].resolve(response("<svg>six</svg>", 500));
    await flush();
    lib.setParam("a", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS);
    expect(lib.getState().items[0].job).not.toBeNull();
    lib.setParam("a", "detail", 6);
    expect(lib.getState().items[0].job).toBeNull();
    expect(calls[1].signal.aborted).toBe(true);
    calls[1].resolve(response("<svg>seven</svg>", 100));
    await flush();
    const a = lib.getState().items[0];
    expect(a.shown).toBe(traceKey(a));
    expect(a.traces[a.shown as string].svg).toBe("<svg>six</svg>");
    expect(needsUpdate(a)).toBe(false);
    // B: Balanced is picked while Auto runs; Auto lands with Logo as its pick
    lib.generate("b");
    lib.pickPreset("b", PRESETS[1]);
    calls[2].resolve(
      response("<svg>logo</svg>", 900, {
        auto: { vexel: { engine: "vexel", pick: "logo", reason: "", candidates: [
          { preset: "balanced", label: "Balanced", svg: "<svg>balanced</svg>", elapsed_ms: 800, stats: {} },
          { preset: "logo", label: "Logo & icon", svg: "<svg>logo</svg>", elapsed_ms: 900, stats: {} },
        ] } },
      }),
    );
    await flush();
    const b = lib.getState().items[1];
    expect(b.preset).toBe("balanced");
    expect(b.params).toEqual({ detail: 6, min_region: 8 });
    expect(b.traces.auto.svg).toBe("<svg>logo</svg>");
    expect(b.shown).toBe(traceKey(b));
    expect(b.traces[b.shown as string].svg).toBe("<svg>balanced</svg>");
    expect(b.auto?.pick).toBe("logo");
  });

  it("Cancel, a changed setting and removal all end a pending live update", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b"), ok("c")]);
    for (const id of ["a", "b", "c"]) {
      lib.pickPreset(id, PRESETS[1]);
      lib.generate(id);
    }
    calls.forEach((c) => c.resolve(response("<svg/>", 100)));
    await flush();
    expect(platform.vectorize).toHaveBeenCalledTimes(3);
    lib.setParam("a", "detail", 7);
    lib.setParam("b", "detail", 7);
    lib.setParam("c", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS / 2);
    lib.cancel("a");
    lib.setSettings({ ...DEFAULT_SETTINGS, liveUpdate: false });
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(3);
    lib.setSettings(DEFAULT_SETTINGS);
    lib.setParam("c", "detail", 8);
    lib.remove("c");
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(3);
  });

  it("picking Auto again restores its pick's values", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.generate("a");
    calls[0].resolve(
      response("<svg>logo</svg>", 900, {
        auto: { vexel: { engine: "vexel", pick: "logo", reason: "", candidates: [{ preset: "logo", label: "Logo & icon", svg: "<svg>logo</svg>", elapsed_ms: 900, stats: {} }] } },
      }),
    );
    await flush();
    lib.pickPreset("a", PRESETS[1]);
    expect(lib.getState().items[0].params).toEqual({ detail: 6, min_region: 8 });
    lib.pickPreset("a", PRESETS[0]);
    const item = lib.getState().items[0];
    expect(item.params).toEqual({ detail: 10, min_region: 16 });
    expect(item.shown).toBe("auto");
  });

  it("counts the images whose vector on screen has not been exported or copied since it was traced", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, { ...DEFAULT_SETTINGS, liveUpdate: false });
    lib.add([ok("a"), ok("b")]);
    expect(unexportedCount(lib.getState())).toBe(0);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    // a trace still running has nothing to export yet
    expect(unexportedCount(lib.getState())).toBe(0);
    calls[0].resolve(response("<svg>a</svg>", 100));
    await flush();
    const first = traceKey(lib.getState().items[0]);
    expect(lib.getState().items[0].exported).toBeNull();
    expect(unexportedCount(lib.getState())).toBe(1);
    lib.markExported([{ id: "a", key: first }, { id: "gone", key: "x" }]);
    expect(lib.getState().items[0].exported).toBe(first);
    expect(unexportedCount(lib.getState())).toBe(0);
    // traced again with other settings: the new vector is not exported
    lib.setParam("a", "detail", 9);
    lib.generate("a");
    calls[1].resolve(response("<svg>a9</svg>", 100));
    await flush();
    expect(unexportedCount(lib.getState())).toBe(1);
    // back to the settings that were exported: that vector was
    lib.pickPreset("a", PRESETS[1]);
    expect(lib.getState().items[0].shown).toBe(first);
    expect(unexportedCount(lib.getState())).toBe(0);
    // removing the image takes its count with it
    lib.setParam("a", "detail", 9);
    expect(unexportedCount(lib.getState())).toBe(1);
    lib.remove("a");
    expect(unexportedCount(lib.getState())).toBe(0);
  });

  it("notifies subscribers and hands out a new state object on every change", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    const seen: unknown[] = [];
    const off = lib.subscribe(() => seen.push(lib.getState()));
    lib.add([ok("a")]);
    lib.select("a");
    off();
    lib.add([ok("b")]);
    expect(seen).toHaveLength(2);
    expect(seen[0]).not.toBe(seen[1]);
  });
});
