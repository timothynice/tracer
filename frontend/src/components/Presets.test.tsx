import { fireEvent, render, screen } from "@testing-library/react";

import { Presets, activePreset } from "./Presets";
import { PRESETS } from "@/test/server";

const defaults = { threshold: 128, invert: false, turnpolicy: "minority", alphamax: 1 };

test("marks the preset the current values match, and only that one", () => {
  // The balanced preset adds nothing, so it matches exactly when nothing is changed.
  expect(activePreset(PRESETS, defaults, defaults)).toBe("balanced");
  expect(activePreset(PRESETS, { ...defaults, threshold: 200, invert: true }, defaults)).toBe("crisp");
  expect(activePreset(PRESETS, { ...defaults, threshold: 77 }, defaults)).toBeNull();
});

test("picking a preset hands back its whole bundle", () => {
  const onPick = vi.fn();
  render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={onPick} />);
  expect(screen.getByRole("button", { name: /balanced/i })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: /crisp/i }));
  expect(onPick).toHaveBeenCalledWith(PRESETS[1]);
});

test("shows what the chosen preset costs, so the choice is informed", () => {
  const { rerender } = render(<Presets presets={PRESETS} defaults={defaults} values={defaults} onPick={() => {}} />);
  expect(screen.getByText("ΔE 0.54 · 6 paths")).toBeInTheDocument();
  rerender(<Presets presets={PRESETS} defaults={defaults} values={{ ...defaults, threshold: 200, invert: true }} onPick={() => {}} />);
  expect(screen.getByText("ΔE 0.67 · 5 paths")).toBeInTheDocument();
});
