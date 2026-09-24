/** MSW handlers mirroring the backend contract, for component and hook tests. */
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { API_URL, type AutoCandidate, type AutoResult, type EngineDescription, type Preset } from "@/lib/api";

export const ENGINES: EngineDescription[] = [
  {
    id: "potrace",
    label: "Potrace",
    description: "Classic black & white outline tracing.",
    primary: true,
    params: {
      properties: {
        threshold: { type: "integer", default: 128, minimum: 0, maximum: 255, description: "Luminance cut-off", ui: { control: "slider", group: "Bitmap" } },
        invert: { type: "boolean", default: false, description: "Trace light regions", ui: { control: "toggle", group: "Bitmap" } },
        turnpolicy: { type: "string", default: "minority", enum: ["black", "white", "minority", "majority"], ui: { control: "select", group: "Cleanup", label: "Turn policy" } },
        alphamax: { type: "number", default: 1, minimum: 0, maximum: 1.3334, ui: { control: "slider", step: 0.05, group: "Curves", label: "Corner smoothing" } },
      },
    },
    defaults: { threshold: 128, invert: false, turnpolicy: "minority", alphamax: 1 },
  },
  {
    id: "vtracer",
    label: "VTracer",
    description: "Colour-preserving tracing.",
    primary: false,
    params: {
      properties: {
        color_precision: { type: "integer", default: 6, minimum: 1, maximum: 8, ui: { control: "slider", group: "Colour", label: "Colour precision" } },
        corner_threshold: { type: "integer", default: 60, minimum: 0, maximum: 180, ui: { control: "slider", group: "Curves", unit: "°" } },
      },
    },
    defaults: { color_precision: 6, corner_threshold: 60 },
  },
];

// Like the backend's list: Auto first (it has no params of its own), then the
// candidates it tries, then a style preset it never picks.
export const PRESETS: Preset[] = [
  { id: "auto", label: "Auto", engine: "potrace", kind: "auto", description: "Tries Balanced and Crisp, keeps the cleanest.", detail: "ΔE 0.52 · 5 paths", sample: "auto.png", params: {} },
  { id: "balanced", label: "Balanced", engine: "potrace", kind: "preset", auto_candidate: true, description: "Everything on.", detail: "ΔE 0.54 · 6 paths", sample: "balanced.png", params: {} },
  { id: "crisp", label: "Crisp", engine: "potrace", kind: "preset", auto_candidate: true, description: "Harder threshold.", detail: "ΔE 0.67 · 5 paths", sample: "logo.png", params: { threshold: 200, invert: true } },
  { id: "poster", label: "Poster", engine: "potrace", kind: "preset", description: "A style, never picked by Auto.", detail: "ΔE 1.2 · 3 paths", sample: "flat.png", params: { threshold: 90 } },
];

export const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M16 16h32v32H16z" fill="#000"/></svg>';

/** The trace an Auto candidate returns: the fixture SVG, tagged with its preset. */
export const candidateSvg = (preset: string) => SVG.replace("<svg ", `<svg data-trace="${preset}" `);

const STATS = { paths: 1, nodes: 4, bytes: SVG.length, gradients: 0, unique_fills: 1 };

function scores(delta_e: number, artifact_index: number, issues: string[], shapes: number) {
  return { delta_e, edge_f1: 0.97, artifact_index, clean: issues.length === 0, issues, shapes, pinholes: issues.length ? 2 : 0, slivers: 0, wobble: 0, inflections: 0, uneven_rects: 0 };
}

/** What /vectorize with auto=true answers for an engine with candidates: every candidate, Crisp chosen. */
export function autoResult(engine: string): AutoResult {
  const cands = PRESETS.filter((p) => p.engine === engine && p.auto_candidate);
  const table: Record<string, ReturnType<typeof scores>> = {
    balanced: scores(0.54, 3.2, ["2 pinholes"], 6),
    crisp: scores(0.61, 0, [], 5),
  };
  const candidates: AutoCandidate[] = cands.map((p) => ({
    preset: p.id, label: p.label, svg: candidateSvg(p.id), elapsed_ms: 10, stats: STATS,
    parameters: { ...ENGINES.find((e) => e.id === engine)!.defaults, ...p.params }, scores: table[p.id] ?? null,
  }));
  return { engine, pick: "crisp", reason: "the cleanest at the same fidelity", candidates };
}

export const handlers = [
  http.get(`${API_URL}/health`, () => HttpResponse.json({ status: "ok", version: "0.2.0", engines: ["potrace", "vtracer"], vexel: "rust" })),
  http.get(`${API_URL}/engines`, () => HttpResponse.json(ENGINES)),
  http.get(`${API_URL}/presets`, () => HttpResponse.json(PRESETS)),
  http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: "a".repeat(32), width: 64, height: 64, format: "PNG" })),
  http.post(`${API_URL}/vectorize`, async ({ request }) => {
    const form = await request.formData();
    const engines = String(form.get("engines") || "potrace,vtracer").split(",").filter(Boolean);
    const params = JSON.parse(String(form.get("parameters") || "{}")) as Record<string, Record<string, unknown>>;
    const results: Record<string, unknown> = Object.fromEntries(engines.map((e) => [e, { svg: SVG, elapsed_ms: 12.5, stats: STATS }]));
    if (form.get("auto") !== "true") {
      return HttpResponse.json({ success: true, image_id: form.get("image_id"), width: 64, height: 64, results, parameters_used: params });
    }
    // Auto: engines with candidates are traced once per candidate and answer with the chosen one.
    const withCandidates = engines.filter((e) => PRESETS.some((p) => p.engine === e && p.auto_candidate));
    if (!withCandidates.length) {
      return HttpResponse.json({ detail: { code: "auto_unavailable", message: "Auto has no candidates for the selected engines" } }, { status: 400 });
    }
    const auto = Object.fromEntries(withCandidates.map((e) => [e, autoResult(e)]));
    for (const [e, a] of Object.entries(auto)) {
      const pick = a.candidates.find((c) => c.preset === a.pick)!;
      results[e] = { svg: pick.svg, elapsed_ms: pick.elapsed_ms, stats: pick.stats };
      params[e] = pick.parameters ?? {};
    }
    return HttpResponse.json({ success: true, image_id: form.get("image_id"), width: 64, height: 64, results, parameters_used: params, auto });
  }),
];

export const server = setupServer(...handlers);
