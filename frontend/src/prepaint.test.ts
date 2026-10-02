import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";

// the script in index.html that sets the theme before first paint
const script = /<script>([\s\S]*?)<\/script>/.exec(readFileSync("index.html", "utf8"))![1];
const run = () => new Function(script)();

afterEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("the pre-paint theme script", () => {
  it("reads the mirrored appearance first", () => {
    localStorage.setItem("studi0trace.appearance", "dark");
    localStorage.setItem("studi0trace.settings", JSON.stringify({ appearance: "light" }));
    run();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("falls back to the browser's saved settings", () => {
    localStorage.setItem("studi0trace.settings", JSON.stringify({ appearance: "dark" }));
    run();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("is light when light is saved, and follows the system for system or nothing", () => {
    document.documentElement.classList.add("dark");
    localStorage.setItem("studi0trace.appearance", "light");
    run();
    expect(document.documentElement).not.toHaveClass("dark");
    localStorage.clear();
    window.matchMedia = ((q: string) => ({ matches: q.includes("dark"), media: q })) as typeof window.matchMedia;
    run();
    expect(document.documentElement).toHaveClass("dark");
  });
});
