import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState, SAMPLES } from "./EmptyState";

afterEach(() => vi.restoreAllMocks());

describe("EmptyState", () => {
  it("asks for images and opens the panel", () => {
    const onOpen = vi.fn();
    render(<EmptyState onOpen={onOpen} onSample={vi.fn()} />);
    expect(screen.getByText("Drop images here")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open…" }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("opens a sample as a file", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Blob([new Uint8Array([1, 2])], { type: "image/png" })));
    const onSample = vi.fn();
    render(<EmptyState onOpen={vi.fn()} onSample={onSample} />);
    fireEvent.click(screen.getByRole("button", { name: SAMPLES[0].label }));
    await waitFor(() => expect(onSample).toHaveBeenCalledOnce());
    expect((onSample.mock.calls[0][0] as File).name).toBe(SAMPLES[0].name);
  });
});
