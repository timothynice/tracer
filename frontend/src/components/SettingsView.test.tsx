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
});
