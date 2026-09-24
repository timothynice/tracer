import { fireEvent, render, screen, within } from "@testing-library/react";

import { Presets, activePreset, autoChoice } from "./Presets";
import { autoResult, candidateSvg, PRESETS } from "@/test/server";
import type { AutoResult } from "@/lib/api";

const defaults = { threshold: 128, invert: false, turnpolicy: "minority", alphamax: 1 };
const row = (id: string) => document.querySelector(`[data-preset="${id}"]`) as HTMLElement;

test("marks the preset the current values match, and only that one; Auto never matches values", () => {
  // The balanced preset adds nothing, so it matches exactly when nothing is changed.
  expect(activePreset(PRESETS, defaults, defaults)).toBe("balanced");
  expect(activePreset(PRESETS, { ...defaults, threshold: 200, invert: true }, defaults)).toBe("crisp");
  expect(activePreset(PRESETS, { ...defaults, threshold: 77 }, defaults)).toBeNull();
});

test("picking a preset hands back its whole bundle", () => {
  const onPick = vi.fn();
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={onPick} />);
  expect(row("balanced")).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(row("crisp"));
  expect(onPick).toHaveBeenCalledWith(PRESETS[2]);
  fireEvent.click(row("auto"));
  expect(onPick).toHaveBeenCalledWith(PRESETS[0]);
});

test("shows what the chosen preset costs, so the choice is informed", () => {
  const { rerender } = render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} />);
  expect(screen.getByText("ΔE 0.54 · 6 paths")).toBeInTheDocument();
  rerender(<Presets presets={PRESETS} defaults={defaults} values={{ ...defaults, threshold: 200, invert: true }} onPick={() => {}} />);
  expect(screen.getByText("ΔE 0.67 · 5 paths")).toBeInTheDocument();
});

test("Auto comes first, then its candidates; style presets sit apart and say Auto never picks them", () => {
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} active="auto" />);
  const ids = [...document.querySelectorAll("[data-preset]")].map((el) => el.getAttribute("data-preset"));
  expect(ids).toEqual(["auto", "balanced", "crisp", "poster"]);
  expect(row("auto")).toHaveAttribute("aria-pressed", "true");
  // Balanced's values match, but Auto is what is on.
  expect(row("balanced")).toHaveAttribute("aria-pressed", "false");
  expect(within(row("auto")).getByText("Tries 2 presets and keeps the cleanest")).toBeInTheDocument();
  expect(screen.getByText(/never picked by Auto/)).toBeInTheDocument();
  expect(screen.getByText("Tries Balanced and Crisp, keeps the cleanest.")).toBeInTheDocument();
});

test("while Auto runs it says what it is doing", () => {
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} active="auto" autoRunning />);
  expect(within(row("auto")).getByText("Trying 2 presets on your image…")).toBeInTheDocument();
});

test("after an Auto run: which preset it chose and why, and every candidate's own result on this image", () => {
  const auto = autoResult("potrace");
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} active="auto" auto={auto} />);
  expect(autoChoice(auto, PRESETS)).toBe("chose Crisp — the cleanest at the same fidelity");
  expect(within(row("auto")).getByText("Chose Crisp — the cleanest at the same fidelity")).toBeInTheDocument();

  // The chosen row is labelled; the others are not.
  expect(within(row("crisp")).getByText("Auto's pick")).toBeInTheDocument();
  expect(within(row("balanced")).queryByText("Auto's pick")).toBeNull();

  // Each candidate: fidelity, size, and whether it came out clean.
  expect(within(row("crisp")).getByText("ΔE 0.61 · 5 shapes")).toBeInTheDocument();
  expect(within(row("crisp")).getByText("clean").closest("[data-clean]")).toHaveAttribute("data-clean", "true");
  expect(within(row("balanced")).getByText("ΔE 0.54 · 6 shapes")).toBeInTheDocument();
  expect(within(row("balanced")).getByText("2 pinholes").closest("[data-clean]")).toHaveAttribute("data-clean", "false");
  expect(row("balanced").title).toContain("On this image: 2 pinholes");

  // Thumbnails show this image traced each candidate's way, not the stock sample.
  const thumb = row("crisp").querySelector("img")!;
  expect(thumb).toHaveAttribute("data-own");
  expect(decodeURIComponent(thumb.getAttribute("src")!)).toContain(candidateSvg("crisp"));
  // A preset Auto did not try keeps its sample and shows no result.
  expect(row("poster").querySelector("img")).toHaveAttribute("src", "/presets/flat.png");
  expect(within(row("poster")).queryByText(/ΔE/)).toBeNull();
});

test("a candidate that failed says so, and one Auto could not choose from says why", () => {
  const auto: AutoResult = {
    ...autoResult("potrace"),
    pick: null,
    reason: "every candidate failed",
    candidates: autoResult("potrace").candidates.map((c) => ({ ...c, svg: null, scores: null, error: { code: "engine_failed", message: "boom" } })),
  };
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} active="auto" auto={auto} />);
  expect(within(row("auto")).getByText("Couldn't choose — every candidate failed")).toBeInTheDocument();
  expect(within(row("crisp")).getByText("Failed: boom")).toBeInTheDocument();
  expect(screen.queryByText("Auto's pick")).toBeNull();
});
