/** The Mac app: Tauri commands (Tasks 2–5 of plan 2) and the events the app sends. */
import { invoke, type InvokeArgs } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { ApiError, apiErrorFromBody, type EngineDescription, type Preset, type VectorizeResponse } from "@/lib/api";
import { DEFAULT_SETTINGS, type Drift, type ExportAllResult, type MenuCommand, type OpenFailure, type OpenOutcome, type Platform, type RedrawPhase, type Roughness, type Settings } from "./types";

interface CommandError {
  status: number;
  body: unknown;
}
interface OpenedDto {
  id: string;
  name: string;
  path: string | null;
  width: number;
  height: number;
  format: string;
}
interface FailureDto {
  name: string;
  path: string | null;
  error: CommandError;
}
type OutcomeDto = { ok: OpenedDto } | { failed: FailureDto };

const toFailure = (d: FailureDto): OpenFailure => ({ name: d.name, path: d.path, error: toApiError(d.error) });

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err && typeof err === "object" && "status" in err && "body" in err) {
    const e = err as CommandError;
    return apiErrorFromBody(e.status, e.body);
  }
  return new ApiError("io_error", typeof err === "string" ? err : ((err as Error | null)?.message ?? "The app could not do that"));
}

async function call<T>(cmd: string, args?: InvokeArgs, headers?: Record<string, string>): Promise<T> {
  try {
    return await invoke<T>(cmd, args, headers ? { headers } : undefined);
  } catch (err) {
    throw toApiError(err);
  }
}

/** Listen to an app event; the returned function stops listening, even before `listen` has resolved. */
function on<T>(event: string, cb: (payload: T) => void): () => void {
  let off: UnlistenFn | null = null;
  let done = false;
  void listen<T>(event, (e) => cb(e.payload)).then((f) => {
    if (done) f();
    else off = f;
  });
  return () => {
    done = true;
    off?.();
  };
}

/** Listen to an app event and resolve once the listener is registered, so nothing sent after this returns is missed. */
async function listening<T>(event: string, cb: (payload: T) => void): Promise<() => void> {
  return listen<T>(event, (e) => cb(e.payload));
}

const redrawCancelled = () => new ApiError("cancelled", "The redraw was cancelled", 499);

export function nativePlatform(): Platform {
  const previews = new Map<string, string>();
  // AI redraw: the redraw waiting for a decision, by image, and the entry that keeps an image's original while its source is a redraw
  const pendingRedraws = new Map<string, string>();
  const originals = new Map<string, string>();
  const forget = (id: string) => {
    const url = previews.get(id);
    if (url) URL.revokeObjectURL(url);
    previews.delete(id);
  };

  // Paths from outside the page. The app holds the ones that came before the page listened; they are taken once
  // per page load, after the first listener is registered, and go to whichever listener is current when they
  // arrive, or wait in `buffer` for the next one, so delivery never depends on when a mount comes or goes.
  let current: ((paths: string[]) => void) | null = null;
  let buffer: string[] = [];
  let taken = false;
  const deliver = (paths: string[]) => {
    if (!paths.length) return;
    if (current) current(paths);
    else buffer.push(...paths);
  };

  async function outcomes(dtos: OutcomeDto[]): Promise<OpenOutcome[]> {
    return Promise.all(
      dtos.map(async (d): Promise<OpenOutcome> => {
        if ("failed" in d) return { failed: toFailure(d.failed) };
        let url = previews.get(d.ok.id);
        if (!url) {
          const buf = await call<ArrayBuffer>("read_image", { id: d.ok.id });
          url = URL.createObjectURL(new Blob([buf]));
          previews.set(d.ok.id, url);
        }
        return { ok: { ...d.ok, previewUrl: url } };
      }),
    );
  }

  return {
    kind: "native",
    canExportPdf: true,
    engines: () => call<EngineDescription[]>("engines"),
    presets: () => call<Preset[]>("presets"),
    pickImages: async () => outcomes(await call<OutcomeDto[]>("pick_images")),
    openFiles: async (files) =>
      outcomes(await Promise.all(files.map(async (f) => call<OutcomeDto>("open_bytes", new Uint8Array(await f.arrayBuffer()), { "x-name": encodeURIComponent(f.name) })))),
    openPaths: async (paths, opts) => outcomes(await call<OutcomeDto[]>("open_paths", { paths, downscale: !!opts?.downscale })),
    closeImage(id) {
      forget(id);
      const waiting = pendingRedraws.get(id);
      if (waiting) forget(waiting);
      pendingRedraws.delete(id);
      const original = originals.get(id);
      if (original) forget(original);
      originals.delete(id);
      void invoke("close_image", { id }).catch(() => {});
    },
    async vectorize(req, { signal, onPhase }) {
      if (signal.aborted) throw new ApiError("cancelled", "The trace was cancelled", 499);
      // listening before the command is sent: a phase the app reports at once must not be missed
      const offPhase = await listening<{ job: string; phase: "tracing" }>("trace-phase", (p) => {
        if (p.job === req.job) onPhase?.(p.phase);
      }).catch(() => () => {});
      const abort = () => void invoke("cancel_trace", { job: req.job }).catch(() => {});
      try {
        if (signal.aborted) throw new ApiError("cancelled", "The trace was cancelled", 499);
        // an abort that lands before the app has the job is remembered there (the queue's tombstones)
        signal.addEventListener("abort", abort, { once: true });
        return await call<VectorizeResponse>("vectorize", { imageId: req.imageId, parameters: req.parameters, auto: req.auto, job: req.job });
      } finally {
        offPhase();
        signal.removeEventListener("abort", abort);
      }
    },
    exportFile: (file, settings) =>
      call<string | null>("export_file", file.bytes, {
        "x-kind": file.kind,
        "x-name": encodeURIComponent(file.name),
        "x-image": file.imageId,
        "x-destination": settings.exportTo,
        "x-reveal": settings.revealAfterExport ? "1" : "0",
      }),
    exportAll: (files, settings) => call<ExportAllResult | null>("export_all", { items: files, reveal: settings.revealAfterExport }),
    copyText: (text) => call<void>("copy_text", { text }),
    reveal: (path) => call<void>("reveal", { path }),
    loadSettings: async () => ({ ...DEFAULT_SETTINGS, ...(await call<Partial<Settings>>("load_settings")) }),
    saveSettings: (settings) => call<Settings>("save_settings", { settings }),
    onSettings: (cb) => on<Settings>("settings-changed", cb),
    onMenu: (cb) => on<{ id: MenuCommand }>("menu", (p) => cb(p.id)),
    onOpenPaths(cb) {
      let off: UnlistenFn | null = null;
      let done = false;
      current = cb;
      if (buffer.length) cb(buffer.splice(0));
      void listen<string[]>("open-paths", (e) => {
        if (!done) cb(e.payload);
      }).then((f) => {
        if (done) {
          f();
          return;
        }
        off = f;
        if (taken) return;
        taken = true;
        void call<string[]>("take_pending_opens").then(deliver, () => {
          taken = false;
        });
      });
      return () => {
        done = true;
        if (current === cb) current = null;
        off?.();
      };
    },
    onOpenFailures: (cb) => on<FailureDto[]>("open-failures", (ds) => cb(ds.map(toFailure))),
    onDragState: (cb) => on<boolean>("drag-state", cb),
    setMenuState: (state) => void invoke("set_menu_state", { state }).catch(() => {}),
    confirmClear: (unexported) => call<boolean>("confirm_clear", { unexported }),
    openSettingsWindow: () => {
      void invoke("open_settings_window").catch(() => {});
      return true;
    },
    windowRole: () => (getCurrentWindow().label === "settings" ? "settings" : "main"),
    redrawKeyStatus: () => call<boolean>("redraw_key_status"),
    setRedrawKey: (key) => call<void>("set_redraw_key", { key }),
    deleteRedrawKey: () => call<void>("delete_redraw_key"),
    onRedrawKey: (cb) => on<boolean>("redraw-key", cb),
    imageRoughness: (id) => call<Roughness>("image_roughness", { id }),
    async redrawImage(id, { signal, onPhase }) {
      if (signal.aborted) throw redrawCancelled();
      // Try again: the redraw it replaces is let go of here, as the app lets it go
      const previous = pendingRedraws.get(id);
      if (previous) forget(previous);
      pendingRedraws.delete(id);
      const offPhase = await listening<{ id: string; phase: RedrawPhase }>("redraw-phase", (p) => {
        if (p.id === id && !signal.aborted) onPhase?.(p.phase);
      }).catch(() => () => {});
      const abort = () => void invoke("cancel_redraw", { id }).catch(() => {});
      try {
        if (signal.aborted) throw redrawCancelled();
        signal.addEventListener("abort", abort, { once: true });
        const answer = await call<{ redraw: OpenedDto; drift: Drift }>("redraw_image", { id });
        if (signal.aborted) {
          // a reply that landed as it was cancelled: let it go
          void invoke("discard_redraw", { id }).catch(() => {});
          throw redrawCancelled();
        }
        const [read] = await outcomes([{ ok: answer.redraw }]);
        if (signal.aborted) {
          // cancelled while the reply was being read: the preview just made and the app's copy both go
          forget(answer.redraw.id);
          void invoke("discard_redraw", { id }).catch(() => {});
          throw redrawCancelled();
        }
        if (!("ok" in read)) throw new ApiError("bad_reply", "The redraw could not be read");
        pendingRedraws.set(id, answer.redraw.id);
        return { redraw: read.ok, drift: answer.drift };
      } finally {
        offPhase();
        signal.removeEventListener("abort", abort);
      }
    },
    async acceptRedraw(id) {
      const redrawId = pendingRedraws.get(id);
      const answer = await call<{ image: OpenedDto; original: OpenedDto }>("accept_redraw", { id });
      pendingRedraws.delete(id);
      const shown = previews.get(id);
      // the first redraw used: the image's preview becomes its original's; a later one replaces the redraw shown
      if (originals.has(id)) {
        if (shown) URL.revokeObjectURL(shown);
      } else if (shown) previews.set(answer.original.id, shown);
      originals.set(id, answer.original.id);
      const redrawUrl = redrawId ? previews.get(redrawId) : undefined;
      if (redrawId) previews.delete(redrawId);
      if (redrawUrl) previews.set(id, redrawUrl);
      else previews.delete(id);
      return {
        image: { ...answer.image, previewUrl: previews.get(id) ?? "" },
        original: { ...answer.original, previewUrl: previews.get(answer.original.id) ?? "" },
      };
    },
    async discardRedraw(id) {
      const waiting = pendingRedraws.get(id);
      if (waiting) forget(waiting);
      pendingRedraws.delete(id);
      await call<boolean>("discard_redraw", { id });
    },
    async revertRedraw(id) {
      const answer = await call<OpenedDto>("revert_redraw", { id });
      const originalId = originals.get(id);
      const back = originalId ? previews.get(originalId) : undefined;
      forget(id);
      if (originalId) previews.delete(originalId);
      originals.delete(id);
      let url = back;
      if (!url) url = URL.createObjectURL(new Blob([await call<ArrayBuffer>("read_image", { id })]));
      previews.set(id, url);
      return { ...answer, previewUrl: url };
    },
  };
}
