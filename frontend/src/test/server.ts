/** MSW handlers mirroring the backend contract, for component and hook tests. */
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { API_URL, type EngineDescription, type Preset } from "@/lib/api";

export const ENGINES: EngineDescription[] = [
  {
    id: "potrace",
    label: "Potrace",
    description: "Classic black & white outline tracing.",
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
    params: {
      properties: {
        color_precision: { type: "integer", default: 6, minimum: 1, maximum: 8, ui: { control: "slider", group: "Colour", label: "Colour precision" } },
        corner_threshold: { type: "integer", default: 60, minimum: 0, maximum: 180, ui: { control: "slider", group: "Curves", unit: "°" } },
      },
    },
    defaults: { color_precision: 6, corner_threshold: 60 },
  },
];

export const PRESETS: Preset[] = [
  { id: "balanced", label: "Balanced", engine: "potrace", description: "Everything on.", detail: "ΔE 0.54 · 6 paths", sample: "balanced.png", params: {} },
  { id: "crisp", label: "Crisp", engine: "potrace", description: "Harder threshold.", detail: "ΔE 0.67 · 5 paths", sample: "logo.png", params: { threshold: 200, invert: true } },
];

export const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M16 16h32v32H16z" fill="#000"/></svg>';

export const handlers = [
  http.get(`${API_URL}/health`, () => HttpResponse.json({ status: "ok", version: "0.2.0", engines: ["potrace", "vtracer"] })),
  http.get(`${API_URL}/engines`, () => HttpResponse.json(ENGINES)),
  http.get(`${API_URL}/presets`, () => HttpResponse.json(PRESETS)),
  http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: "a".repeat(32), width: 64, height: 64, format: "PNG" })),
  http.post(`${API_URL}/vectorize`, async ({ request }) => {
    const form = await request.formData();
    const engines = String(form.get("engines") || "potrace,vtracer").split(",").filter(Boolean);
    const params = JSON.parse(String(form.get("parameters") || "{}")) as Record<string, Record<string, unknown>>;
    const results = Object.fromEntries(
      engines.map((e) => [e, { svg: SVG, elapsed_ms: 12.5, stats: { paths: 1, nodes: 4, bytes: SVG.length, gradients: 0, unique_fills: 1 } }]),
    );
    return HttpResponse.json({ success: true, image_id: form.get("image_id"), width: 64, height: 64, results, parameters_used: params });
  }),
];

export const server = setupServer(...handlers);
