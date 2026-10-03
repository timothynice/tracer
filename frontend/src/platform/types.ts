/** What differs between the Mac app and a browser, behind one interface the UI calls. */
import type { ApiError, EngineDescription, ParamValues, Preset, VectorizeResponse } from "@/lib/api";

export interface OpenedImage {
  /** The image's id (a hash of the file): the same file opened twice is one image. */
  id: string;
  name: string;
  /** Where the file is; null for samples, pastes and everything in a browser. */
  path: string | null;
  width: number;
  height: number;
  format: string;
  /** A blob: URL of the source, owned by the platform until `closeImage`. */
  previewUrl: string;
}

export interface OpenFailure {
  name: string;
  path: string | null;
  error: ApiError;
}

export type OpenOutcome = { ok: OpenedImage } | { failed: OpenFailure };

export type Phase = "queued" | "tracing";

export interface TraceRequest {
  imageId: string;
  /** The vexel values; ignored when `auto`. */
  parameters: ParamValues;
  auto: boolean;
  /** Names the job, so it can be cancelled and its phase reported. */
  job: string;
}

export interface TraceOptions {
  signal: AbortSignal;
  onPhase?: (phase: Phase) => void;
}

export interface Settings {
  appearance: "system" | "light" | "dark";
  exportTo: "ask" | "beside";
  revealAfterExport: boolean;
  traceOnOpen: boolean;
  liveUpdate: boolean;
  /** Paths, most recent first; only the app changes it. */
  recent: string[];
}

export const DEFAULT_SETTINGS: Settings = { appearance: "system", exportTo: "ask", revealAfterExport: false, traceOnOpen: false, liveUpdate: true, recent: [] };

export type ViewMode = "split" | "side" | "overlay" | "vector";

export type MenuCommand =
  | "export-svg"
  | "export-png-1"
  | "export-png-2"
  | "export-png-4"
  | "export-all"
  | "reveal"
  | "copy-svg"
  | "zoom-in"
  | "zoom-out"
  | "zoom-actual"
  | "zoom-fit"
  | "mode-split"
  | "mode-side"
  | "mode-overlay"
  | "mode-vector"
  | "toggle-sidebar"
  | "toggle-inspector"
  | "generate"
  | "cancel"
  | "remove"
  | "clear";

export interface MenuState {
  hasItems: boolean;
  hasImage: boolean;
  hasVector: boolean;
  anyVector: boolean;
  hasPath: boolean;
  /** The selected image is tracing. */
  tracing: boolean;
  /** Any image is tracing (closing or quitting then asks first). */
  anyTracing: boolean;
  /** Images whose vector on screen has not been exported or copied since it was traced (closing asks first). */
  unexported: number;
  mode: ViewMode;
  sidebar: boolean;
  inspector: boolean;
}

export interface ExportFile {
  kind: "svg" | "png";
  imageId: string;
  /** The suggested file name, e.g. "logo.svg" or "logo@2x.png". */
  name: string;
  bytes: Uint8Array<ArrayBuffer>;
}

/** Export All's answer: the paths written, and the images that could not be (`id` is the one the call was given). */
export interface ExportAllResult {
  written: string[];
  failed: { id: string; name: string; message: string }[];
}

export interface Platform {
  readonly kind: "native" | "web";
  engines(signal?: AbortSignal): Promise<EngineDescription[]>;
  presets(signal?: AbortSignal): Promise<Preset[]>;
  /** The open panel (a file picker in a browser), and what was chosen, opened. */
  pickImages(): Promise<OpenOutcome[]>;
  /** Files with no path: samples, pastes, browser drops. */
  openFiles(files: File[]): Promise<OpenOutcome[]>;
  /** Paths the app was given (drops, Open With, recent files); `downscale` fits them to the side cap. */
  openPaths(paths: string[], opts?: { downscale?: boolean }): Promise<OpenOutcome[]>;
  closeImage(id: string): void;
  vectorize(req: TraceRequest, opts: TraceOptions): Promise<VectorizeResponse>;
  /** The path written; null when the save panel was cancelled. In a browser the file is downloaded and this is its name. */
  exportFile(file: ExportFile, settings: Settings): Promise<string | null>;
  /** Writes every file it can; null when the folder panel was cancelled. */
  exportAll(files: { id: string; name: string; svg: string }[], settings: Settings): Promise<ExportAllResult | null>;
  copyText(text: string): Promise<void>;
  reveal(path: string): Promise<void>;
  loadSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<Settings>;
  onSettings(cb: (settings: Settings) => void): () => void;
  onMenu(cb: (command: MenuCommand) => void): () => void;
  /** Paths to open from outside the page; the ones that came before the page listened are handed over first. */
  onOpenPaths(cb: (paths: string[]) => void): () => void;
  /** Opens that failed before reaching the page (a drop with nothing to open). Native only. */
  onOpenFailures(cb: (failures: OpenFailure[]) => void): () => void;
  /** Files are being dragged over the window (true) or no longer are (false). Native only. */
  onDragState(cb: (over: boolean) => void): () => void;
  setMenuState(state: MenuState): void;
  /** Settings in a window of its own; false where there is none (a browser shows its own sheet). */
  openSettingsWindow(): boolean;
  windowRole(): "main" | "settings";
}
