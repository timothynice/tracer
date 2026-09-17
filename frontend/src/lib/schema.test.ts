import { groupSpecs, humanize, isDefault, normalizeValues, paramsKey, sanitize, specFor, specsFor } from "./schema";
import { ENGINES } from "@/test/server";

test("humanize", () => {
  expect(humanize("color_precision")).toBe("Color precision");
  expect(humanize("threshold")).toBe("Threshold");
});

test("specs derive control, bounds, labels and steps from schema", () => {
  const specs = specsFor(ENGINES[0]);
  const byName = Object.fromEntries(specs.map((s) => [s.name, s]));
  expect(byName.threshold).toMatchObject({ control: "slider", type: "integer", min: 0, max: 255, step: 1, group: "Bitmap", label: "Threshold" });
  expect(byName.invert).toMatchObject({ control: "toggle", type: "boolean", default: false });
  expect(byName.turnpolicy).toMatchObject({ control: "select", options: ["black", "white", "minority", "majority"], label: "Turn policy" });
  expect(byName.alphamax).toMatchObject({ control: "slider", type: "number", step: 0.05, label: "Corner smoothing" });
});

test("controls are inferred when ui hints are missing", () => {
  expect(specFor("x", { type: "boolean", default: true }).control).toBe("toggle");
  expect(specFor("x", { type: "string", enum: ["a", "b"], default: "a" }).control).toBe("select");
  expect(specFor("x", { type: "number", minimum: 0, maximum: 1, default: 0.5 })).toMatchObject({ control: "slider", step: 0.01 });
  expect(specFor("x", { type: "number", minimum: 0, maximum: 10, default: 4 }).step).toBe(0.1);
  expect(specFor("x", { type: "integer", default: 3 }).group).toBe("General");
});

test("groups keep first-seen order", () => {
  expect(groupSpecs(specsFor(ENGINES[0])).map((g) => g.group)).toEqual(["Bitmap", "Cleanup", "Curves"]);
});

test("sanitize clamps, rounds and falls back to defaults", () => {
  const specs = specsFor(ENGINES[0]);
  const threshold = specs.find((s) => s.name === "threshold")!;
  expect(sanitize(threshold, 300)).toBe(255);
  expect(sanitize(threshold, "12.7")).toBe(13);
  expect(sanitize(threshold, "nope")).toBe(128);
  const policy = specs.find((s) => s.name === "turnpolicy")!;
  expect(sanitize(policy, "sideways")).toBe("minority");
  const invert = specs.find((s) => s.name === "invert")!;
  expect(sanitize(invert, "yes")).toBe(false);
});

test("normalizeValues fills defaults and drops unknown keys", () => {
  const specs = specsFor(ENGINES[0]);
  const v = normalizeValues(specs, { threshold: 999, legacy: 1 });
  expect(v).toEqual({ threshold: 255, invert: false, turnpolicy: "minority", alphamax: 1 });
  expect(isDefault(specs, v)).toBe(false);
  expect(isDefault(specs, normalizeValues(specs, undefined))).toBe(true);
});

test("paramsKey is order independent", () => {
  expect(paramsKey({ b: 1, a: 2 })).toBe(paramsKey({ a: 2, b: 1 }));
});
