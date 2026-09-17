/** Turn an engine's JSON Schema (from GET /engines) into renderable control specs. */
import type { EngineDescription, ParamValues, SchemaProperty } from "./api";

export type ControlKind = "slider" | "select" | "toggle";
export type ValueType = "integer" | "number" | "boolean" | "string";

export interface ParamSpec {
  name: string;
  label: string;
  description?: string;
  control: ControlKind;
  group: string;
  type: ValueType;
  default: unknown;
  min?: number;
  max?: number;
  step: number;
  unit?: string;
  options?: string[];
}

export const DEFAULT_GROUP = "General";

export function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function valueType(prop: SchemaProperty): ValueType {
  const t = Array.isArray(prop.type) ? prop.type.find((x) => x !== "null") : prop.type;
  if (t === "integer" || t === "number" || t === "boolean" || t === "string") return t;
  if (prop.enum) return "string";
  return typeof prop.default === "number" ? "number" : typeof prop.default === "boolean" ? "boolean" : "string";
}

export function inferControl(prop: SchemaProperty): ControlKind {
  if (prop.ui?.control) return prop.ui.control;
  if (prop.enum) return "select";
  const t = valueType(prop);
  if (t === "boolean") return "toggle";
  return "slider";
}

function niceStep(min: number | undefined, max: number | undefined, type: ValueType): number {
  if (type === "integer") return 1;
  if (min === undefined || max === undefined) return 0.1;
  const span = max - min;
  if (span <= 2) return 0.01;
  if (span <= 20) return 0.1;
  return 1;
}

export function specFor(name: string, prop: SchemaProperty): ParamSpec {
  const type = valueType(prop);
  const min = prop.minimum ?? prop.exclusiveMinimum;
  const max = prop.maximum ?? prop.exclusiveMaximum;
  return {
    name,
    label: prop.ui?.label ?? prop.title ?? humanize(name),
    description: prop.description,
    control: inferControl(prop),
    group: prop.ui?.group ?? DEFAULT_GROUP,
    type,
    default: prop.default,
    min,
    max,
    step: prop.ui?.step ?? niceStep(min, max, type),
    unit: prop.ui?.unit,
    options: prop.enum?.map(String),
  };
}

export function specsFor(engine: EngineDescription): ParamSpec[] {
  return Object.entries(engine.params.properties ?? {}).map(([name, prop]) => specFor(name, prop));
}

export interface ParamGroup {
  group: string;
  specs: ParamSpec[];
}

/** Groups in first-seen order, so the backend's field order drives the layout. */
export function groupSpecs(specs: ParamSpec[]): ParamGroup[] {
  const groups: ParamGroup[] = [];
  for (const spec of specs) {
    const existing = groups.find((g) => g.group === spec.group);
    if (existing) existing.specs.push(spec);
    else groups.push({ group: spec.group, specs: [spec] });
  }
  return groups;
}

/** Coerce and clamp a value for a spec; returns the spec default when unusable. */
export function sanitize(spec: ParamSpec, value: unknown): unknown {
  switch (spec.type) {
    case "boolean":
      return typeof value === "boolean" ? value : Boolean(spec.default);
    case "string":
      if (spec.options) return spec.options.includes(String(value)) ? String(value) : spec.default;
      return typeof value === "string" ? value : spec.default;
    default: {
      const n = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(n)) return spec.default;
      let v = spec.type === "integer" ? Math.round(n) : n;
      if (spec.min !== undefined) v = Math.max(spec.min, v);
      if (spec.max !== undefined) v = Math.min(spec.max, v);
      return v;
    }
  }
}

/** Fill defaults and drop unknown keys so persisted state survives schema changes. */
export function normalizeValues(specs: ParamSpec[], values: ParamValues | undefined): ParamValues {
  const out: ParamValues = {};
  for (const spec of specs) {
    out[spec.name] = values && spec.name in values ? sanitize(spec, values[spec.name]) : spec.default;
  }
  return out;
}

/** Stable key for caching: sorted keys, JSON. */
export function paramsKey(values: ParamValues): string {
  return JSON.stringify(Object.fromEntries(Object.keys(values).sort().map((k) => [k, values[k]])));
}

export function isDefault(specs: ParamSpec[], values: ParamValues): boolean {
  return specs.every((s) => values[s.name] === s.default);
}
