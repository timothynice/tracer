import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "styles.css"), "utf8");
const block = (selector: string) => css.slice(css.indexOf(`${selector} {`)).split("}")[0];
const tokens = (text: string) => [...text.matchAll(/--([a-z-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%/g)].map((m) => ({ name: m[1], h: Number(m[2]), s: Number(m[3]) }));
// the accent, the status colours, the brand yellow and plain white are not greys
const COLOURED = new Set(["primary-foreground", "brand-accent", "success", "warning", "info", "destructive", "destructive-foreground"]);

describe("the design tokens", () => {
  it.each([":root", ".dark"])("%s: every neutral sits on one cool hue", (selector) => {
    const greys = tokens(block(selector)).filter((t) => !COLOURED.has(t.name));
    expect(greys.length).toBeGreaterThan(10);
    for (const t of greys) {
      expect(t.h, t.name).toBeGreaterThanOrEqual(214);
      expect(t.h, t.name).toBeLessThanOrEqual(216);
      expect(t.s, t.name).toBeGreaterThan(10);
    }
  });

  it("tints the vibrancy instead of leaving the window transparent", () => {
    expect(css).toMatch(/html\.native body \{[^}]*hsl\(var\(--window\) \/ 0\.5\)/);
    expect(css).toMatch(/html\.native\.dark body \{[^}]*hsl\(var\(--window\) \/ 0\.55\)/);
  });
});
