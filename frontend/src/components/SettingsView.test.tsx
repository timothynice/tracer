import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_SETTINGS } from "@/platform/types";
import { SettingsView } from "./SettingsView";

describe("SettingsView", () => {
  it("shows the settings and hands back each change whole", () => {
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "dark" });
    fireEvent.click(screen.getByRole("radio", { name: "Next to the original" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, exportTo: "beside" });
    fireEvent.click(screen.getByRole("switch", { name: "Trace new images straight away" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, traceOnOpen: true });
    expect(screen.getByRole("switch", { name: "Update automatically when tracing is quick" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("switch", { name: "Show in Finder after export" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, revealAfterExport: true });
  });
  it("checks for updates by default, and the switch turns it off", () => {
    expect(DEFAULT_SETTINGS.checkForUpdates).toBe(true);
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name: "Check for updates automatically" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, checkForUpdates: false });
  });
  it("names its sections General, Export and Tracing, never the same word as the first row", () => {
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={vi.fn()} />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["General", "Export", "Tracing"]);
    expect(screen.getAllByText("Appearance")).toHaveLength(1);
  });
  it("a radiogroup is one tab stop, moved between its options by the arrow keys", () => {
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} />);
    const system = screen.getByRole("radio", { name: "System" });
    const light = screen.getByRole("radio", { name: "Light" });
    const dark = screen.getByRole("radio", { name: "Dark" });
    expect([system, light, dark].map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    fireEvent.keyDown(system, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "light" });
    expect(light).toHaveFocus();
    fireEvent.keyDown(system, { key: "ArrowLeft" }); // wraps to the last
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "dark" });
    expect(dark).toHaveFocus();
    fireEvent.keyDown(system, { key: "ArrowDown" });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "light" });
    fireEvent.keyDown(system, { key: "ArrowUp" });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "dark" });
  });

  it("the tab stop follows the checked option", () => {
    render(<SettingsView settings={{ ...DEFAULT_SETTINGS, appearance: "dark" }} onChange={vi.fn()} />);
    expect(["System", "Light", "Dark"].map((n) => screen.getByRole("radio", { name: n }).tabIndex)).toEqual([-1, -1, 0]);
  });
  it("leaves a shortcut's arrow keys alone", () => {
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} />);
    for (const mod of ["metaKey", "altKey", "ctrlKey"]) fireEvent.keyDown(screen.getByRole("radio", { name: "System" }), { key: "ArrowRight", [mod]: true });
    expect(onChange).not.toHaveBeenCalled();
  });
});
