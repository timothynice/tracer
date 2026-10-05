/**
 * The images open in the window, each with its own settings, every trace seen for it and the
 * job running for it. Framework-free: React reads it through `useLibrary`, tests call it directly.
 */
import { ApiError, type AutoResult, type EngineDescription, type ParamValues, type Preset, type VectorizeResponse } from "@/lib/api";
import { normalizeValues, paramsKey, specsFor } from "@/lib/schema";
import type { OpenedImage, OpenFailure, OpenOutcome, Phase, Platform, Settings } from "@/platform/types";

export const ENGINE = "vexel";
/** A trace quicker than this is run again by itself when its settings move. */
export const LIVE_MS = 2000;
/** How long a control must rest before a live update traces. */
export const DEBOUNCE_MS = 250;

export interface TraceAnswer {
  svg: string;
  elapsedMs: number;
  stats: Record<string, number>;
}

export interface Job {
  id: string;
  /** The settings key it traces. */
  key: string;
  startedAt: number;
  phase: Phase;
}

export interface ImageItem {
  image: OpenedImage;
  /** "auto", a preset's id, or null once a control has been moved by hand. */
  preset: string | null;
  /** The engine's values the panel shows; after an Auto run, those of Auto's pick. */
  params: ParamValues;
  /** Every answer seen for this image, by settings key ("auto" or `paramsKey(params)`). */
  traces: Record<string, TraceAnswer>;
  /** The key of the answer on screen; null before the first trace. */
  shown: string | null;
  /** The key of the trace last exported or copied; null before the first. */
  exported: string | null;
  job: Job | null;
  /** The last trace that failed, and the settings key it was for: see `errorOf`. */
  error: ApiError | null;
  errorKey: string | null;
  /** The last Auto run on this image. */
  auto: AutoResult | null;
}

export interface LibraryState {
  items: ImageItem[];
  /** Files that could not be opened, kept in the sidebar with what went wrong. */
  failed: OpenFailure[];
  selected: string | null;
}

export interface Catalog {
  engine: EngineDescription;
  presets: Preset[];
}

export type LibraryPlatform = Pick<Platform, "vectorize" | "closeImage" | "openPaths">;

export interface Library {
  getState(): LibraryState;
  subscribe(listener: () => void): () => void;
  selectedItem(): ImageItem | null;
  add(outcomes: OpenOutcome[]): void;
  select(id: string | null): void;
  selectNext(delta: 1 | -1): void;
  remove(id: string): void;
  clear(): void;
  dismissFailure(index: number): void;
  /** Open a too-large file again, scaled to fit the cap, in place of its failure. */
  downscale(index: number): Promise<void>;
  pickPreset(id: string, preset: Preset): void;
  setParam(id: string, name: string, value: unknown): void;
  generate(id: string): void;
  cancel(id: string): void;
  setSettings(settings: Settings): void;
  /** These traces were exported or copied (by image id and trace key). */
  markExported(marks: ExportMark[]): void;
}

export interface ExportMark {
  id: string;
  key: string;
}

export function traceKey(item: Pick<ImageItem, "preset" | "params">): string {
  return item.preset === "auto" ? "auto" : paramsKey(item.params);
}

/** The error to show: a failure belongs to the settings that failed, and says nothing about any others. */
export function errorOf(item: Pick<ImageItem, "preset" | "params" | "error" | "errorKey">): ApiError | null {
  return item.error && item.errorKey === traceKey(item) ? item.error : null;
}

/** The job tracing the settings now on the controls; a job for other settings runs on in the background and is not it. */
export function currentJob(item: ImageItem): Job | null {
  return item.job && item.job.key === traceKey(item) ? item.job : null;
}

export function shownAnswer(item: ImageItem): TraceAnswer | null {
  return item.shown ? (item.traces[item.shown] ?? null) : null;
}

/** Images whose vector on screen has not been exported or copied since it was traced: what quitting would lose. */
export function unexportedCount(state: LibraryState): number {
  return state.items.filter((i) => i.shown !== null && i.exported !== i.shown).length;
}

/** The settings have moved since the trace on screen, and no job is tracing them. */
export function needsUpdate(item: ImageItem): boolean {
  const key = traceKey(item);
  return item.shown !== null && item.shown !== key && item.job?.key !== key && !item.traces[key];
}

function isCancel(err: unknown): boolean {
  return (err instanceof ApiError && err.code === "cancelled") || (err as Error | null)?.name === "AbortError";
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError("engine_crashed", (err as Error | null)?.message || "The trace failed", 0, err);
}

let jobCounter = 0;
const newJobId = () => `job-${Date.now().toString(36)}-${(jobCounter += 1)}`;

export function createLibrary(platform: LibraryPlatform, catalog: Catalog, initialSettings: Settings): Library {
  const specs = specsFor(catalog.engine);
  const resolve = (params: ParamValues) => normalizeValues(specs, { ...catalog.engine.defaults, ...params });
  const defaults = resolve({});
  const presetById = new Map(catalog.presets.map((p) => [p.id, p]));

  let state: LibraryState = { items: [], failed: [], selected: null };
  let settings = initialSettings;
  const listeners = new Set<() => void>();
  const controllers = new Map<string, AbortController>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();

  const emit = () => listeners.forEach((l) => l());
  const set = (next: LibraryState) => {
    state = next;
    emit();
  };
  const find = (id: string) => state.items.find((i) => i.image.id === id);
  const patch = (id: string, change: Partial<ImageItem> | ((item: ImageItem) => Partial<ImageItem>)) =>
    set({ ...state, items: state.items.map((i) => (i.image.id === id ? { ...i, ...(typeof change === "function" ? change(i) : change) } : i)) });

  const clearTimer = (id: string) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
  };

  const abortJob = (item: ImageItem | undefined) => {
    if (!item?.job) return;
    controllers.get(item.job.id)?.abort();
    controllers.delete(item.job.id);
  };

  const answerOf = (res: VectorizeResponse): TraceAnswer | ApiError => {
    const r = res.results[ENGINE];
    if (!r || r.error || !r.svg) return new ApiError(r?.error?.code ?? "engine_failed", r?.error?.message ?? "The engine returned no vector");
    return { svg: r.svg, elapsedMs: r.elapsed_ms ?? 0, stats: r.stats ?? {} };
  };

  const finish = (id: string, jobId: string, key: string, res: VectorizeResponse) => {
    const item = find(id);
    if (!item || item.job?.id !== jobId) return; // superseded or cancelled
    const answer = answerOf(res);
    if (answer instanceof ApiError) {
      patch(id, { job: null, error: answer, errorKey: key });
      return;
    }
    const traces = { ...item.traces, [key]: answer };
    const auto = res.auto?.[ENGINE] ?? null;
    // the answer is always kept; it takes the screen and the controls only if the settings still are the ones it traced
    const current = traceKey(item) === key;
    let params = item.params;
    if (auto) {
      for (const c of auto.candidates) {
        const preset = presetById.get(c.preset);
        if (preset && c.svg) traces[paramsKey(resolve(preset.params))] = { svg: c.svg, elapsedMs: c.elapsed_ms ?? 0, stats: c.stats ?? {} };
      }
      const pick = auto.pick ? presetById.get(auto.pick) : undefined;
      if (pick && current) params = resolve(pick.params);
    }
    // settings that moved on while it ran show their own trace if one is known (a candidate, say), else stay as they are
    const now = current ? key : traces[traceKey({ preset: item.preset, params })] ? traceKey({ preset: item.preset, params }) : item.shown;
    patch(id, { job: null, error: null, errorKey: null, traces, shown: now, auto: auto ?? item.auto, params });
  };

  const fail = (id: string, jobId: string, err: unknown) => {
    const item = find(id);
    if (!item || item.job?.id !== jobId) return;
    // kept with the key it failed for: shown only while the settings are those again
    patch(id, isCancel(err) ? { job: null } : { job: null, error: toApiError(err), errorKey: item.job.key });
  };

  const generate = (id: string) => {
    const item = find(id);
    if (!item) return;
    const key = traceKey(item);
    if (item.traces[key]) {
      patch(id, { shown: key, error: null, errorKey: null });
      return;
    }
    if (item.job?.key === key) return;
    abortJob(item);
    const jobId = newJobId();
    const controller = new AbortController();
    controllers.set(jobId, controller);
    patch(id, { job: { id: jobId, key, startedAt: Date.now(), phase: "queued" }, error: null, errorKey: null });
    const auto = key === "auto";
    platform
      .vectorize(
        { imageId: id, parameters: auto ? {} : item.params, auto, job: jobId },
        {
          signal: controller.signal,
          onPhase: (phase) => {
            const now = find(id);
            if (now?.job?.id === jobId) patch(id, { job: { ...now.job, phase } });
          },
        },
      )
      .then((res) => finish(id, jobId, key, res))
      .catch((err) => fail(id, jobId, err))
      .finally(() => controllers.delete(jobId));
  };

  /** After the settings of a traced image move: show what is known, or trace if traces here are quick. */
  const settle = (id: string, debounce: boolean) => {
    const item = find(id);
    if (!item) return;
    const key = traceKey(item);
    if (item.traces[key]) {
      // a job for other settings runs on: its answer is kept as any other, and it takes nothing from the screen
      patch(id, { shown: key });
      return;
    }
    const last = item.shown ? item.traces[item.shown] : undefined;
    if (!settings.liveUpdate || !last || last.elapsedMs >= LIVE_MS) return;
    clearTimer(id);
    if (!debounce) {
      generate(id);
      return;
    }
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id);
        if (settings.liveUpdate) generate(id);
      }, DEBOUNCE_MS),
    );
  };

  const selectAfterRemoving = (id: string): string | null => {
    const at = state.items.findIndex((i) => i.image.id === id);
    if (state.selected !== id) return state.selected;
    const rest = state.items.filter((i) => i.image.id !== id);
    return rest[Math.min(at, rest.length - 1)]?.image.id ?? null;
  };

  const lib: Library = {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    selectedItem: () => (state.selected ? (find(state.selected) ?? null) : null),

    add(outcomes) {
      const items = [...state.items];
      const failed = [...state.failed];
      const added: string[] = [];
      let selected = state.selected;
      for (const o of outcomes) {
        if ("failed" in o) {
          if (!failed.some((f) => f.name === o.failed.name && f.path === o.failed.path)) failed.push(o.failed);
          continue;
        }
        selected = o.ok.id;
        if (items.some((i) => i.image.id === o.ok.id)) continue;
        items.push({ image: o.ok, preset: "auto", params: defaults, traces: {}, shown: null, exported: null, job: null, error: null, errorKey: null, auto: null });
        added.push(o.ok.id);
      }
      set({ items, failed, selected });
      if (settings.traceOnOpen) added.forEach(generate);
    },

    select(id) {
      if (id === null || find(id)) set({ ...state, selected: id });
    },

    selectNext(delta) {
      const at = state.items.findIndex((i) => i.image.id === state.selected);
      const next = state.items[Math.max(0, Math.min(state.items.length - 1, at + delta))];
      if (next) set({ ...state, selected: next.image.id });
    },

    remove(id) {
      const item = find(id);
      if (!item) return;
      abortJob(item);
      clearTimer(id);
      platform.closeImage(id);
      set({ ...state, selected: selectAfterRemoving(id), items: state.items.filter((i) => i.image.id !== id) });
    },

    clear() {
      for (const item of state.items) {
        abortJob(item);
        clearTimer(item.image.id);
        platform.closeImage(item.image.id);
      }
      set({ items: [], failed: [], selected: null });
    },

    dismissFailure(index) {
      set({ ...state, failed: state.failed.filter((_, i) => i !== index) });
    },

    async downscale(index) {
      const failure = state.failed[index];
      if (!failure?.path) return;
      const outcomes = await platform.openPaths([failure.path], { downscale: true });
      set({ ...state, failed: state.failed.filter((f) => f !== failure) });
      lib.add(outcomes);
    },

    pickPreset(id, preset) {
      const item = find(id);
      if (!item) return;
      if (preset.kind === "auto") {
        // back to Auto shows its pick's values again, not those of the preset tried since
        const pick = item.auto?.pick ? presetById.get(item.auto.pick) : undefined;
        patch(id, pick ? { preset: "auto", params: resolve(pick.params) } : { preset: "auto" });
      }
      else patch(id, { preset: preset.id, params: resolve(preset.params) });
      settle(id, false);
    },

    setParam(id, name, value) {
      const item = find(id);
      if (!item) return;
      const params = normalizeValues(specs, { ...item.params, [name]: value });
      // a control left as it was moves nothing: the preset (Auto) stays, and nothing traces
      if (paramsKey(params) === paramsKey(item.params)) return;
      patch(id, { preset: null, params });
      settle(id, true);
    },

    generate,

    cancel(id) {
      clearTimer(id);
      const item = find(id);
      if (!item?.job) return;
      abortJob(item);
      patch(id, { job: null });
    },

    setSettings(next) {
      settings = next;
      if (!settings.liveUpdate) [...timers.keys()].forEach(clearTimer);
    },

    markExported(marks) {
      const byId = new Map(marks.map((m) => [m.id, m.key]));
      if (!state.items.some((i) => byId.has(i.image.id))) return;
      set({ ...state, items: state.items.map((i) => (byId.has(i.image.id) ? { ...i, exported: byId.get(i.image.id)! } : i)) });
    },
  };
  return lib;
}
