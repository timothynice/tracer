// ImageTracer.js, one image per call, for the bench's `imagetracer` adapter.
//
//   node imagetracer.js IN.png PRESET_OR_JSON [SEED]  ->  stdout: {"svg": "...", "ms": 123.4}
//
// PRESET_OR_JSON is one of ImageTracer's built-in option presets ('default',
// 'posterized2', 'detailed', ...) or a JSON options object. Math.random is
// replaced by a seeded generator first: the presets that sample colours at
// random ('randomsampling1', 'randomsampling2') are then reproducible.
// `ms` covers PNG decode, tracing and SVG serialisation, not Node's startup.
// Needs `imagetracerjs` and `pngjs` resolvable from NODE_PATH or this directory.
"use strict";
const fs = require("fs");

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const [input, spec = "default", seed = "1"] = process.argv.slice(2);
Math.random = mulberry32(parseInt(seed, 10) || 1);
const ImageTracer = require("imagetracerjs");
const { PNG } = require("pngjs");

const t0 = process.hrtime.bigint();
const png = PNG.sync.read(fs.readFileSync(input));
const imgd = { width: png.width, height: png.height, data: png.data };
let options = spec.trim().startsWith("{") ? JSON.parse(spec) : spec;
if (typeof options === "string" && !(options.toLowerCase() in ImageTracer.optionpresets)) {
  process.stderr.write(`unknown ImageTracer preset: ${options}\n`);
  process.exit(2);
}
const svg = ImageTracer.imagedataToSVG(imgd, options);
const ms = Number(process.hrtime.bigint() - t0) / 1e6;
process.stdout.write(JSON.stringify({ svg, ms, version: ImageTracer.versionnumber }));
