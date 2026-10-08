import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { paramsKey } from "@/lib/schema";
import type { ImageItem } from "@/state/library";
import { TraceButton } from "./TraceButton";

const base: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 4, height: 4, format: "PNG", previewUrl: "blob:a" },
  preset: "balanced",
  params: { detail: 6 },
  traces: {},
  shown: null,
  exported: null,
  job: null,
  error: null,
  errorKey: null,
  auto: null,
};
const answer = { svg: "<svg/>", elapsedMs: 900, stats: {} };

describe("TraceButton", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] }));
  afterEach(() => vi.useRealTimers());

  it("generates an untraced image", () => {
    const onGenerate = vi.fn();
    render(<TraceButton item={base} candidates={4} onGenerate={onGenerate} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Vector/ }));
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("is Up to date when the trace on screen is the settings', and Update when they moved", () => {
    const key = paramsKey({ detail: 6 });
    const { rerender } = render(<TraceButton item={{ ...base, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Generate Vector/ })).toBeNull();
    rerender(<TraceButton item={{ ...base, params: { detail: 9 }, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Update Vector/ })).toBeEnabled();
  });

  it("while a job runs the one button names the phase, counts the seconds and cancels", () => {
    const onCancel = vi.fn();
    render(<TraceButton item={{ ...base, preset: "auto", job: { id: "j", key: "auto", startedAt: Date.now(), phase: "tracing" } }} candidates={4} onGenerate={vi.fn()} onCancel={onCancel} />);
    expect(screen.getByRole("button", { name: "Trying 4 styles… 0 s" })).toHaveAttribute("title", "Cancel Trace (⌘.)");
    expect(screen.getByTestId("sweep")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    fireEvent.click(screen.getByRole("button", { name: "Trying 4 styles… 3 s" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("a job for other settings is not this button's: it shows the settings' own state", () => {
    const key = paramsKey({ detail: 6 });
    render(<TraceButton item={{ ...base, traces: { [key]: answer }, shown: key, job: { id: "j", key: "auto", startedAt: Date.now(), phase: "tracing" } }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByTestId("sweep")).toBeNull();
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
  });

  it("a queued job says so, without the sweep, and still cancels", () => {
    const onCancel = vi.fn();
    render(<TraceButton item={{ ...base, job: { id: "j", key: paramsKey({ detail: 6 }), startedAt: Date.now(), phase: "queued" } }} candidates={4} onGenerate={vi.fn()} onCancel={onCancel} />);
    expect(screen.queryByTestId("sweep")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Queued…" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
