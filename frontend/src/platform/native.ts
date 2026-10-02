/** The Mac app: Tauri commands (Tasks 2–5 of plan 2) and the events the app sends. */
import { invoke, type InvokeArgs } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { ApiError, apiErrorFromBody, type EngineDescription, type Health, type Preset, type VectorizeResponse } from "@/lib/api";
import { DEFAULT_SETTINGS, type MenuCommand, type OpenOutcome, type Platform, type Settings } from "./types";

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
type OutcomeDto = { ok: OpenedDto } | { failed: { name: string; path: string | null; error: CommandError } };

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

// Paths taken from the app after their listener had gone (React StrictMode mounts, unmounts and mounts again):
// the next listener gets them.
let unclaimed: string[] = [];

export function nativePlatform(): Platform {
  const previews = new Map<string, string>();

  async function outcomes(dtos: OutcomeDto[]): Promise<OpenOutcome[]> {
    return Promise.all(
      dtos.map(async (d): Promise<OpenOutcome> => {
        if ("failed" in d) return { failed: { name: d.failed.name, path: d.failed.path, error: toApiError(d.failed.error) } };
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
    health: () => call<Health>("health"),
    engines: () => call<EngineDescription[]>("engines"),
    presets: () => call<Preset[]>("presets"),
    pickImages: async () => outcomes(await call<OutcomeDto[]>("pick_images")),
    openFiles: async (files) =>
      outcomes(await Promise.all(files.map(async (f) => call<OutcomeDto>("open_bytes", new Uint8Array(await f.arrayBuffer()), { "x-name": encodeURIComponent(f.name) })))),
    openPaths: async (paths, opts) => outcomes(await call<OutcomeDto[]>("open_paths", { paths, downscale: !!opts?.downscale })),
    closeImage(id) {
      const url = previews.get(id);
      if (url) URL.revokeObjectURL(url);
      previews.delete(id);
      void invoke("close_image", { id }).catch(() => {});
    },
    async vectorize(req, { signal, onPhase }) {
      if (signal.aborted) throw new ApiError("cancelled", "The trace was cancelled", 499);
      const offPhase = on<{ job: string; phase: "tracing" }>("trace-phase", (p) => {
        if (p.job === req.job) onPhase?.(p.phase);
      });
      const abort = () => void invoke("cancel_trace", { job: req.job }).catch(() => {});
      signal.addEventListener("abort", abort, { once: true });
      try {
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
    exportAll: (files, settings) => call<string[] | null>("export_all", { items: files, reveal: settings.revealAfterExport }),
    copyText: (text) => call<void>("copy_text", { text }),
    reveal: (path) => call<void>("reveal", { path }),
    loadSettings: async () => ({ ...DEFAULT_SETTINGS, ...(await call<Partial<Settings>>("load_settings")) }),
    saveSettings: (settings) => call<Settings>("save_settings", { settings }),
    onSettings: (cb) => on<Settings>("settings-changed", cb),
    onMenu: (cb) => on<{ id: MenuCommand }>("menu", (p) => cb(p.id)),
    onOpenPaths(cb) {
      let off: UnlistenFn | null = null;
      let done = false;
      if (unclaimed.length) cb(unclaimed.splice(0));
      void listen<string[]>("open-paths", (e) => cb(e.payload)).then((f) => {
        if (done) {
          f();
          return;
        }
        off = f;
        void call<string[]>("take_pending_opens").then((pending) => {
          if (!pending.length) return;
          if (done) unclaimed.push(...pending);
          else cb(pending);
        });
      });
      return () => {
        done = true;
        off?.();
      };
    },
    onDragState: (cb) => on<boolean>("drag-state", cb),
    setMenuState: (state) => void invoke("set_menu_state", { state }).catch(() => {}),
    openSettingsWindow: () => {
      void invoke("open_settings_window").catch(() => {});
      return true;
    },
    windowRole: () => (getCurrentWindow().label === "settings" ? "settings" : "main"),
  };
}
