/** The browser: the Python server over HTTP, a file picker, downloads and localStorage. A development harness. */
import { ApiError, getEngines, getPresets, uploadImage, vectorize as httpVectorize } from "@/lib/api";
import { hashFile } from "@/lib/hash";
import { downloadBlob } from "@/lib/raster";
import { DEFAULT_SETTINGS, type OpenOutcome, type Platform, type Settings } from "./types";

const SETTINGS_KEY = "studi0trace.settings";
/** AI redraw runs in the Mac app only: the browser harness has no Keychain and no road to OpenAI. */
const noRedraw = () => Promise.reject(new ApiError("unavailable", "AI redraw needs the Mac app"));
export const ACCEPTED = "image/png,image/jpeg,image/gif,image/webp,image/bmp";

function readSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return { ...DEFAULT_SETTINGS, ...(raw ? (JSON.parse(raw) as Partial<Settings>) : {}), recent: [] };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function webPlatform(): Platform {
  // the server's id for each image, and the file, to send again if the server has forgotten it
  const uploads = new Map<string, { file: File; serverId: string; url: string }>();
  const settingsListeners = new Set<(s: Settings) => void>();

  async function openFiles(files: File[]): Promise<OpenOutcome[]> {
    const out: OpenOutcome[] = [];
    for (const file of files) {
      try {
        const id = await hashFile(file);
        const up = await uploadImage(file);
        const url = uploads.get(id)?.url ?? URL.createObjectURL(file);
        uploads.set(id, { file, serverId: up.image_id, url });
        out.push({ ok: { id, name: file.name, path: null, width: up.width, height: up.height, format: up.format, previewUrl: url } });
      } catch (err) {
        out.push({ failed: { name: file.name, path: null, error: err instanceof ApiError ? err : new ApiError("network", String(err)) } });
      }
    }
    return out;
  }

  return {
    kind: "web",
    engines: (signal) => getEngines(signal),
    presets: (signal) => getPresets(signal),
    pickImages: () =>
      new Promise((resolve) => {
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = true;
        input.accept = ACCEPTED;
        input.onchange = () => void openFiles(Array.from(input.files ?? [])).then(resolve);
        input.oncancel = () => resolve([]);
        input.click();
      }),
    openFiles,
    openPaths: async () => {
      throw new ApiError("unsupported", "Opening files by path needs the Mac app");
    },
    closeImage(id) {
      const u = uploads.get(id);
      if (u) URL.revokeObjectURL(u.url);
      uploads.delete(id);
    },
    async vectorize(req, { signal }) {
      const entry = uploads.get(req.imageId);
      if (!entry) throw new ApiError("image_expired", "Upload expired or unknown; upload it again", 404);
      const send = (serverId: string) => httpVectorize({ imageId: serverId, engines: ["vexel"], parameters: { vexel: req.parameters }, auto: req.auto }, signal);
      try {
        return await send(entry.serverId);
      } catch (err) {
        if (!(err instanceof ApiError) || err.code !== "image_expired") throw err;
        const up = await uploadImage(entry.file, signal);
        entry.serverId = up.image_id;
        return send(up.image_id);
      }
    },
    async exportFile(file) {
      downloadBlob(new Blob([file.bytes], { type: file.kind === "png" ? "image/png" : "image/svg+xml" }), file.name);
      return file.name;
    },
    async exportAll(files) {
      for (const f of files) downloadBlob(new Blob([f.svg], { type: "image/svg+xml" }), f.name);
      return { written: files.map((f) => f.name), failed: [] };
    },
    copyText: (text) => navigator.clipboard.writeText(text),
    reveal: async () => {},
    loadSettings: async () => readSettings(),
    async saveSettings(settings) {
      const next = { ...settings, recent: [] };
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
      } catch {
        /* private mode: settings last for the session */
      }
      settingsListeners.forEach((l) => l(next));
      return next;
    },
    onSettings(cb) {
      settingsListeners.add(cb);
      return () => settingsListeners.delete(cb);
    },
    onMenu: () => () => {},
    onOpenPaths: () => () => {},
    onOpenFailures: () => () => {},
    onDragState: () => () => {},
    setMenuState: () => {},
    confirmClear: async (unexported) =>
      window.confirm(`Clear all images?\n\n${unexported === 1 ? "1 traced image has not been exported." : `${unexported} traced images have not been exported.`}`),
    openSettingsWindow: () => false,
    windowRole: () => "main",
    redrawKeyStatus: noRedraw,
    setRedrawKey: noRedraw,
    deleteRedrawKey: noRedraw,
    onRedrawKey: () => () => {},
    imageRoughness: noRedraw,
    redrawImage: noRedraw,
    acceptRedraw: noRedraw,
    discardRedraw: noRedraw,
    revertRedraw: noRedraw,
  };
}
