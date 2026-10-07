import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AutoResult, Preset } from "@/lib/api";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";
import { activePreset, autoChoice, firstSentence, PresetCards } from "./PresetCards";

const defaults = VEXEL.defaults;
const auto: AutoResult = {
  engine: "vexel",
  pick: "logo",
  reason: "the cleanest at the same fidelity",
  candidates: [
    { preset: "balanced", label: "Balanced", svg: "<svg/>", scores: { delta_e: 0.25, edge_f1: 0.9, artifact_index: 3.2, clean: false, issues: ["2 pinholes"], shapes: 6, pinholes: 2, slivers: 0, wobble: 0, inflections: 0, uneven_rects: 0 } },
    { preset: "logo", label: "Logo & icon", svg: "<svg/>", scores: { delta_e: 0.31, edge_f1: 0.9, artifact_index: 0, clean: true, issues: [], shapes: 3, pinholes: 0, slivers: 0, wobble: 0, inflections: 0, uneven_rects: 0 } },
  ],
};

function Controlled({ onPick }: { onPick: (p: Preset) => void }) {
  const [active, setActive] = useState("auto");
  return <PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active={active} auto={null} autoRunning={false} onPick={(p) => { onPick(p); setActive(p.id); }} />;
}

describe("PresetCards", () => {
  it("the arrow keys carry the focus with the pick, so a second press goes on to the next card", () => {
    const onPick = vi.fn();
    render(<Controlled onPick={onPick} />);
    const auto = screen.getByRole("radio", { name: "Auto" });
    auto.focus();
    fireEvent.keyDown(auto, { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[1]);
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Balanced" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[2]);
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Logo & icon" }));
    expect(document.activeElement).toHaveAttribute("aria-checked", "true");
  });

  it("is a radio group with Auto first and the styles folded under Choose a style", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={vi.fn()} />);
    const group = screen.getByRole("radiogroup", { name: "Preset" });
    expect(within(group).getAllByRole("radio")).toHaveLength(1);
    expect(within(group).getByRole("radio", { name: "Auto" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Tries 2 styles and keeps the cleanest faithful one.")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Choose a style" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-label"))).toEqual(["Auto", "Balanced", "Logo & icon", "Flat & poster"]);
    expect(screen.getByRole("radio", { name: "Balanced" })).toHaveAttribute("title", expect.stringContaining("Gradients, shadows, strokes and overlaps all reconstructed."));
  });

  it("opens itself when a style is the active one, and hands back the preset picked", () => {
    const onPick = vi.fn();
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="logo" auto={null} autoRunning={false} onPick={onPick} />);
    expect(screen.getByRole("button", { name: "Choose a style" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("radio", { name: "Logo & icon" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Balanced" }));
    expect(onPick).toHaveBeenCalledWith(VEXEL_PRESETS[1]);
  });

  it("after Auto: what it chose and why, each style's verdict, and the pick marked with the brand dot", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={auto} autoRunning={false} onPick={vi.fn()} />);
    expect(screen.getByLabelText("Auto chose Logo & icon — the cleanest at the same fidelity")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Choose a style" }));
    expect(within(screen.getByRole("radio", { name: "Balanced" })).getByText("2 pinholes")).toBeInTheDocument();
    const logo = screen.getByRole("radio", { name: "Logo & icon" });
    expect(within(logo).getByText("Auto's pick")).toHaveClass("sr-only");
    expect(logo.querySelector(".dot-brand")).toBeInTheDocument();
    expect(within(logo).getByText("Clean")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Balanced" }).querySelector(".dot-brand")).toBeNull();
  });

  it("while Auto runs it says so", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning onPick={vi.fn()} />);
    expect(screen.getByText("Trying 2 styles…")).toBeInTheDocument();
  });

  it("the helpers", () => {
    expect(firstSentence("One thing. Another thing.")).toBe("One thing.");
    expect(firstSentence("No full stop")).toBe("No full stop");
    expect(activePreset(VEXEL_PRESETS, { detail: 10, min_region: 16 }, defaults)).toBe("logo");
    expect(activePreset(VEXEL_PRESETS, { detail: 7, min_region: 8 }, defaults)).toBeNull();
    expect(autoChoice(auto, VEXEL_PRESETS)).toBe("chose Logo & icon — the cleanest at the same fidelity");
  });
});
