/** Typed client for the Studi0Trace backend. Every error becomes an ApiError with a stable code. */

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number = 0,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface Health {
  status: string;
  version: string;
  engines: string[];
  /** Which Vexel implementation is serving: "rust" or "python". Optional so an
   *  older backend still parses. */
  vexel?: string;
}

export interface UiHints {
  control?: "slider" | "select" | "toggle";
  group?: string;
  label?: string;
  step?: number;
  unit?: string;
}

export interface SchemaProperty {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  enum?: unknown[];
  ui?: UiHints;
}

export interface EngineSchema {
  properties: Record<string, SchemaProperty>;
  required?: string[];
}

export interface EngineDescription {
  id: string;
  label: string;
  description: string;
  /** Whether the app shows it. Non-primary engines stay callable for benchmarks. */
  primary: boolean;
  params: EngineSchema;
  defaults: Record<string, unknown>;
}

export interface EngineError {
  code: string;
  message: string;
}

export interface EngineResult {
  svg?: string | null;
  elapsed_ms?: number | null;
  stats?: Record<string, number> | null;
  error?: EngineError | null;
}

/** How one Auto candidate did on this image. */
export interface CandidateScores {
  /** Mean CIEDE2000 against the source; lower is closer. */
  delta_e: number;
  edge_f1: number;
  /** 0 is clean; each clearly visible defect costs about 1. */
  artifact_index: number;
  /** No pinhole, sliver, wobbly edge or uneven rectangle. */
  clean: boolean;
  /** What is visibly wrong, in a designer's words ("3 pinholes", "wobbly edges"). */
  issues: string[];
  shapes: number;
  pinholes: number;
  slivers: number;
  wobble: number;
  inflections: number;
  uneven_rects: number;
}

/** One preset Auto traced with. */
export interface AutoCandidate {
  preset: string;
  label: string;
  svg?: string | null;
  elapsed_ms?: number | null;
  stats?: Record<string, number> | null;
  parameters?: Record<string, unknown> | null;
  scores?: CandidateScores | null;
  error?: EngineError | null;
}

/** What Auto tried for one engine and what it chose; the choice is also that engine's `results` entry. */
export interface AutoResult {
  engine: string;
  pick: string | null;
  /** Finishes the sentence "Auto chose <label> — …". */
  reason: string;
  candidates: AutoCandidate[];
}

export interface VectorizeResponse {
  success: boolean;
  image_id: string;
  width: number;
  height: number;
  results: Record<string, EngineResult>;
  parameters_used: Record<string, Record<string, unknown>>;
  /** Per engine, only when the request asked for Auto. */
  auto?: Record<string, AutoResult> | null;
}

export interface UploadResponse {
  image_id: string;
  width: number;
  height: number;
  format: string;
}

export type ParamValues = Record<string, unknown>;

export interface Preset {
  id: string;
  label: string;
  engine: string;
  description: string;
  /** What the preset measurably costs or buys, from the bench. */
  detail: string;
  sample: string;
  params: ParamValues;
  /** "auto" traces with every candidate and picks per image (request it with `auto`, not its params). */
  kind?: "auto" | "preset";
  /** Whether Auto tries this preset. */
  auto_candidate?: boolean;
}

class TimeoutError extends Error {
  name = "TimeoutError";
}

/** Combine a caller's signal with a timeout without relying on AbortSignal.any/timeout. */
function withTimeout(signal: AbortSignal | undefined, ms: number | undefined): { signal: AbortSignal | undefined; done: () => void } {
  if (!ms) return { signal, done: () => {} };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new TimeoutError("timeout")), ms);
  const onAbort = () => ctl.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort, { once: true });
  if (signal?.aborted) onAbort();
  return {
    signal: ctl.signal,
    done: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    const where = first?.loc?.join(".") ?? "";
    return new ApiError("validation_error", first?.msg ? `${where}: ${first.msg}` : "Invalid parameters", res.status, detail);
  }
  if (detail && typeof detail === "object" && "code" in detail) {
    const d = detail as { code: string; message?: string };
    return new ApiError(d.code, d.message ?? d.code, res.status, detail);
  }
  if (res.status >= 500) {
    // A bare gateway error carries no body; say what it usually means here.
    return new ApiError(`http_${res.status}`, `The server didn't finish the trace (${res.status}). Large or highly detailed images can exhaust it — try a smaller image.`, res.status, body);
  }
  return new ApiError(`http_${res.status}`, typeof detail === "string" ? detail : res.statusText || "Request failed", res.status, body);
}

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
  const timed = withTimeout(signal, timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, signal: timed.signal });
  } catch (err) {
    if (timed.signal?.reason instanceof TimeoutError) throw new ApiError("timeout", "The server did not respond in time");
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError("network", "Could not reach the server", 0, `${(err as Error).name}: ${(err as Error).message}`);
  } finally {
    timed.done();
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export const getHealth = (signal?: AbortSignal, timeoutMs = 4000) => request<Health>("/health", {}, signal, timeoutMs);

export const getEngines = (signal?: AbortSignal) => request<EngineDescription[]>("/engines", {}, signal);

export const getPresets = (signal?: AbortSignal) => request<Preset[]>("/presets", {}, signal);

export function uploadImage(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  return request<UploadResponse>("/uploads", { method: "POST", body: form }, signal);
}

export interface VectorizeArgs {
  imageId: string;
  engines: string[];
  parameters: Record<string, ParamValues>;
  /** Trace with every Auto candidate and let the server pick (engines without candidates trace as usual). */
  auto?: boolean;
}

export function vectorize({ imageId, engines, parameters, auto }: VectorizeArgs, signal?: AbortSignal): Promise<VectorizeResponse> {
  const form = new FormData();
  form.append("image_id", imageId);
  form.append("engines", engines.join(","));
  form.append("parameters", JSON.stringify(parameters));
  if (auto) form.append("auto", "true");
  return request<VectorizeResponse>("/vectorize", { method: "POST", body: form }, signal);
}
