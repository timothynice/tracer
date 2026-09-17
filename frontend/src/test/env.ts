/**
 * jsdom environment that keeps Node's fetch-related globals.
 *
 * jsdom ships its own AbortController/AbortSignal/FormData/Blob/File. Node's
 * fetch (undici) rejects them by `instanceof`, so any request that passes a
 * signal or a multipart body fails under plain jsdom. Vitest replaces these
 * globals when it populates jsdom; we put Node's back afterwards.
 */
import type { Environment } from "vitest/environments";
import { builtinEnvironments } from "vitest/environments";

const KEEP = ["AbortController", "AbortSignal", "FormData", "Blob", "File", "fetch", "Request", "Response", "Headers"] as const;

export default <Environment>{
  name: "jsdom-node-fetch",
  transformMode: "web",
  async setup(global, options) {
    const kept = Object.fromEntries(KEEP.map((k) => [k, (global as Record<string, unknown>)[k]]));
    const env = await builtinEnvironments.jsdom.setup(global, options);
    for (const [k, v] of Object.entries(kept)) {
      if (v !== undefined) (global as Record<string, unknown>)[k] = v;
    }
    return env;
  },
};
