import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";

const toasts = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toasts.error, success: vi.fn() }, Toaster: () => null }));

const hooks = vi.hoisted(() => ({ menu: null as null | ((c: string) => void), opens: null as null | ((p: string[]) => void), states: [] as Record<string, unknown>[], settings: {} as Record<string, unknown> }));

vi.mock("@/platform", async () => {
  const types = await vi.importActual<typeof import("@/platform/types")>("@/platform/types");
  const platform = {
    kind: "native",
    engines: async () => [VEXEL],
    presets: async () => VEXEL_PRESETS,
    pickImages: async () => [],
    openFiles: async () => [],
    openPaths: vi.fn(async (paths: string[]) => paths.map((p) => ({ ok: { id: p, name: p.split("/").pop(), path: p, width: 64, height: 64, format: "PNG", previewUrl: `blob:${p}` } }))),
    closeImage: vi.fn(),
    vectorize: vi.fn(() => new Promise(() => {})),
    exportFile: vi.fn(async () => null),
    exportAll: vi.fn(async () => null),
    copyText: vi.fn(async () => {}),
    reveal: vi.fn(async () => {}),
    loadSettings: async () => ({ ...types.DEFAULT_SETTINGS, ...hooks.settings }),
    saveSettings: async (s: unknown) => s,
    onSettings: () => () => {},
    onMenu: (cb: (c: string) => void) => {
      hooks.menu = cb;
      return () => {};
    },
    onOpenPaths: (cb: (p: string[]) => void) => {
      hooks.opens = cb;
      return () => {};
    },
    onOpenFailures: () => () => {},
    onDragState: () => () => {},
    setMenuState: (s: Record<string, unknown>) => hooks.states.push(s),
    confirmClear: vi.fn(async () => true),
    openSettingsWindow: () => true,
    windowRole: () => "main",
    redrawKeyStatus: vi.fn(async () => false),
    setRedrawKey: vi.fn(async () => {}),
    deleteRedrawKey: vi.fn(async () => {}),
    onRedrawKey: () => () => {},
    imageRoughness: vi.fn(async () => ({ rough: true, reason: "small" })),
    redrawImage: vi.fn(),
    acceptRedraw: vi.fn(),
    discardRedraw: vi.fn(async () => {}),
    revertRedraw: vi.fn(),
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");
const { platform } = await import("@/platform");

const PATH = "/pics/small.png";
const opened = { id: PATH, name: "small.png", path: PATH, width: 64, height: 64, format: "PNG", previewUrl: `blob:${PATH}` };
const redraw = { id: "r1", name: "small.png", path: null, width: 2048, height: 2048, format: "PNG", previewUrl: "blob:r1" };

async function start() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );
  await screen.findByText("Drop images here");
  await waitFor(() => expect(hooks.opens).not.toBeNull());
  act(() => hooks.opens!([PATH]));
  await screen.findByRole("option", { name: /small\.png/ });
}

beforeEach(() => {
  toasts.error.mockClear();
  vi.mocked(platform.redrawImage).mockClear();
  hooks.settings = {};
});

describe("AI redraw in the Mac app", () => {
  it("hints for a small image, asks for a key first, checks the drift, swaps the source on Use redraw and back on Revert", async () => {
    vi.mocked(platform.redrawImage)
      .mockRejectedValueOnce(new ApiError("no_key", "Add your OpenAI API key to use AI redraw.", 401))
      .mockResolvedValueOnce({ redraw, drift: { edgeF1: 0.59, deltaE: 3.8, verdict: "large" } });
    vi.mocked(platform.acceptRedraw).mockResolvedValueOnce({ image: { ...opened, width: 2048, height: 2048, previewUrl: "blob:r1" }, original: { ...opened, id: `${PATH}-original` } });
    vi.mocked(platform.revertRedraw).mockResolvedValueOnce(opened);
    await start();
    expect(await screen.findByText("This image is small — an AI redraw may trace cleaner.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Redraw with AI…" }));
    const sheet = await screen.findByRole("dialog", { name: "Redraw with AI" });
    fireEvent.change(within(sheet).getByLabelText("OpenAI API key"), { target: { value: "sk-test-1" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Save Key and Redraw" }));
    await waitFor(() => expect(platform.setRedrawKey).toHaveBeenCalledWith("sk-test-1"));

    const check = await screen.findByRole("region", { name: "Drift check" });
    expect(within(check).getByText("Large")).toBeInTheDocument();
    expect(within(check).getByText("Edges matched: 59 %")).toBeInTheDocument();
    expect(within(check).getByText("Colour shift: ΔE 3.8")).toBeInTheDocument();
    expect(platform.redrawImage).toHaveBeenCalledTimes(2);
    expect(screen.getByAltText("AI redraw")).toHaveAttribute("src", "blob:r1");
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(within(check).getByRole("button", { name: "Use redraw" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Drift check" })).toBeNull());
    expect(platform.acceptRedraw).toHaveBeenCalledWith(PATH);
    expect(screen.getByAltText("Source raster")).toHaveAttribute("src", "blob:r1");
    expect(screen.getByText("AI redraw")).toBeInTheDocument();
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ isRedraw: true, canRedraw: true }));

    act(() => hooks.menu!("revert-redraw"));
    await waitFor(() => expect(screen.getByAltText("Source raster")).toHaveAttribute("src", `blob:${PATH}`));
    expect(platform.revertRedraw).toHaveBeenCalledWith(PATH);
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ isRedraw: false }));
  });

  it("shows a failed redraw in the app's words, without the consent sheet", async () => {
    vi.mocked(platform.redrawImage).mockRejectedValueOnce(new ApiError("quota", "Your OpenAI account is out of credit or rate limited.", 429));
    await start();
    act(() => hooks.menu!("redraw"));
    expect(await screen.findByText("Your OpenAI account is out of credit or rate limited.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(toasts.error).not.toHaveBeenCalled(); // the inspector shows it: no second telling
  });

  it("words a failed Use redraw or Revert by its code, never by the server's message, and never as an unhandled rejection", async () => {
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    vi.mocked(platform.redrawImage).mockResolvedValueOnce({ redraw, drift: { edgeF1: 0.97, deltaE: 1, verdict: "close" } });
    vi.mocked(platform.acceptRedraw)
      .mockRejectedValueOnce(new ApiError("not_allowed", "sk-secret-123 may not create images", 403))
      .mockResolvedValueOnce({ image: { ...opened, width: 2048, height: 2048, previewUrl: "blob:r1" }, original: { ...opened, id: `${PATH}-original` } });
    vi.mocked(platform.revertRedraw).mockRejectedValueOnce(new ApiError("engine_crashed", "panic: sk-secret-123", 500));
    await start();
    act(() => hooks.menu!("redraw"));
    const check = await screen.findByRole("region", { name: "Drift check" });
    fireEvent.click(within(check).getByRole("button", { name: "Use redraw" }));
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith("OpenAI did not allow this key to create images. Your organization may need to be verified for image models at platform.openai.com."));
    expect(screen.getByRole("region", { name: "Drift check" })).toBeInTheDocument(); // still waiting for its decision
    fireEvent.click(within(screen.getByRole("region", { name: "Drift check" })).getByRole("button", { name: "Use redraw" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Drift check" })).toBeNull());
    act(() => hooks.menu!("revert-redraw"));
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith("The redraw stopped unexpectedly."));
    await new Promise((r) => setTimeout(r, 0));
    expect(toasts.error.mock.calls.flat().join(" ")).not.toContain("sk-secret");
    expect(unhandled).not.toHaveBeenCalled();
    process.off("unhandledRejection", unhandled);
  });

  it("a cancelled redraw says nothing", async () => {
    vi.mocked(platform.redrawImage).mockRejectedValueOnce(new ApiError("cancelled", "The redraw was cancelled", 499));
    await start();
    act(() => hooks.menu!("redraw"));
    await waitFor(() => expect(platform.redrawImage).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(toasts.error).not.toHaveBeenCalled();
  });

  it("with the inspector hidden a failed redraw speaks as a toast, in words by code; a cancel stays silent", async () => {
    vi.mocked(platform.redrawImage)
      .mockRejectedValueOnce(new ApiError("quota", "sk-secret-123 quota", 429))
      .mockRejectedValueOnce(new ApiError("cancelled", "The redraw was cancelled", 499));
    await start();
    act(() => hooks.menu!("toggle-inspector"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Redraw with AI…" })).toBeNull());
    act(() => hooks.menu!("redraw"));
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith("Your OpenAI account is out of credit or rate limited."));
    act(() => hooks.menu!("redraw"));
    await waitFor(() => expect(platform.redrawImage).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 0));
    expect(toasts.error).toHaveBeenCalledTimes(1);
  });

  it("traces the redraw after Use redraw when Trace on open is set, and Show Original returns with Done", async () => {
    hooks.settings = { traceOnOpen: true };
    vi.mocked(platform.vectorize).mockClear();
    vi.mocked(platform.redrawImage).mockResolvedValueOnce({ redraw, drift: { edgeF1: 0.9, deltaE: 3, verdict: "noticeable" } });
    vi.mocked(platform.acceptRedraw).mockResolvedValueOnce({ image: { ...opened, width: 2048, height: 2048, previewUrl: "blob:r1" }, original: { ...opened, id: `${PATH}-original` } });
    await start();
    await waitFor(() => expect(platform.vectorize).toHaveBeenCalledTimes(1)); // the original, on open
    act(() => hooks.menu!("redraw"));
    const check = await screen.findByRole("region", { name: "Drift check" });
    fireEvent.click(within(check).getByRole("button", { name: "Use redraw" }));
    await waitFor(() => expect(platform.vectorize).toHaveBeenCalledTimes(2));
    expect(vi.mocked(platform.vectorize).mock.calls[1][0]).toMatchObject({ imageId: PATH });
    // Show Original: the redraw in use against the original, closed with Done
    act(() => hooks.menu!("show-original"));
    const review = await screen.findByRole("region", { name: "Drift check" });
    expect(within(review).queryByRole("button", { name: "Use redraw" })).toBeNull();
    fireEvent.click(within(review).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Drift check" })).toBeNull());
    expect(screen.getByAltText("Source raster")).toHaveAttribute("src", "blob:r1");
  });
});
