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
    const auto = screen.getByRole("radio", { name: /^Auto/ });
    auto.focus();
    fireEvent.keyDown(auto, { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[1]);
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: /^Balanced/ }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[2]);
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: /^Logo & icon/ }));
    expect(document.activeElement).toHaveAttribute("aria-checked", "true");
  });

  it("is a radio group, Auto first, the styles Auto never picks folded away", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={vi.fn()} />);
    const group = screen.getByRole("radiogroup", { name: "Preset" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);
    expect(radios[0]).toHaveAccessibleName(/^Auto/);
    expect(screen.queryByRole("radio", { name: /^Flat & poster/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More Styles" }));
    expect(screen.getByRole("radio", { name: /^Flat & poster/ })).toBeInTheDocument();
  });

  it("says the first sentence of each description and hands back the preset picked", () => {
    const onPick = vi.fn();
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={onPick} />);
    expect(screen.getByText("Gradients, shadows, strokes and overlaps all reconstructed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /^Logo & icon/ }));
    expect(onPick).toHaveBeenCalledWith(VEXEL_PRESETS[2]);
    fireEvent.keyDown(screen.getByRole("radio", { name: /^Auto/ }), { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[1]);
  });

  it("after Auto: what it chose and why, each candidate's issues, and the pick marked", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={auto} autoRunning={false} onPick={vi.fn()} />);
    expect(screen.getByLabelText("Auto chose Logo & icon — the cleanest at the same fidelity")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Balanced/ })).getByText("2 pinholes")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Logo & icon/ })).getByText("Auto's pick")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Logo & icon/ })).getByText("Clean")).toBeInTheDocument();
  });

  it("while Auto runs it says so", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning onPick={vi.fn()} />);
    expect(screen.getByText("Trying 2 presets on your image…")).toBeInTheDocument();
  });

  it("the helpers", () => {
    expect(firstSentence("One thing. Another thing.")).toBe("One thing.");
    expect(firstSentence("No full stop")).toBe("No full stop");
    expect(activePreset(VEXEL_PRESETS, { detail: 10, min_region: 16 }, defaults)).toBe("logo");
    expect(activePreset(VEXEL_PRESETS, { detail: 7, min_region: 8 }, defaults)).toBeNull();
    expect(autoChoice(auto, VEXEL_PRESETS)).toBe("chose Logo & icon — the cleanest at the same fidelity");
  });
});
