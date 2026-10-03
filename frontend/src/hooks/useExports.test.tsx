import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paramsKey } from "@/lib/schema";
import { DEFAULT_SETTINGS } from "@/platform/types";
import type { ImageItem, LibraryState } from "@/state/library";
import { useExports } from "./useExports";

const mocks = vi.hoisted(() => ({
  platform: { kind: "native", exportFile: vi.fn(), exportAll: vi.fn(), copyText: vi.fn(), reveal: vi.fn() },
  success: vi.fn(),
  error: vi.fn(),
  png: vi.fn(),
}));
vi.mock("@/platform", async (orig) => ({ ...(await orig<typeof import("@/platform")>()), platform: mocks.platform }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));
vi.mock("@/lib/raster", async (orig) => ({ ...(await orig<typeof import("@/lib/raster")>()), svgToPngBlob: mocks.png }));

const key = paramsKey({ detail: 6 });
const item = (id: string, name: string, traced = true): ImageItem => ({
  image: { id, name, path: null, width: 4, height: 2, format: "PNG", previewUrl: `blob:${id}` },
  preset: "balanced",
  params: { detail: 6 },
  traces: traced ? { [key]: { svg: `<svg id="${id}"/>`, elapsedMs: 1, stats: {} } } : {},
  shown: traced ? key : null,
  exported: null,
  job: null,
  error: null,
  errorKey: null,
  auto: null,
});
const a = item("a", "logo.png");
const b = item("b", "photo.jpg");
const c = item("c", "blank.png", false);
const state = (...items: ImageItem[]): LibraryState => ({ items, failed: [], selected: items[0]?.image.id ?? null });
const text = (bytes: Uint8Array) => new TextDecoder().decode(bytes);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.platform.kind = "native";
  mocks.platform.exportFile.mockResolvedValue("/Users/t/logo.svg");
  mocks.platform.exportAll.mockResolvedValue({ written: ["/x/logo.svg"], failed: [] });
  mocks.platform.copyText.mockResolvedValue(undefined);
  mocks.png.mockResolvedValue(new Blob([new Uint8Array([1, 2, 3])]));
});

describe("useExports", () => {
  it("exports the edited SVG as stem.svg", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg edited/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("svg", 1));
    const file = mocks.platform.exportFile.mock.calls[0][0];
    expect(file).toMatchObject({ kind: "svg", imageId: "a", name: "logo.svg" });
    expect(text(file.bytes)).toBe("<svg edited/>");
  });

  it("exports another image as its own trace, not the selected one's edited SVG", async () => {
    const { result } = renderHook(() => useExports(state(a, b), a, "<svg edited/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("svg", 1, b));
    const file = mocks.platform.exportFile.mock.calls[0][0];
    expect(file).toMatchObject({ imageId: "b", name: "photo.svg" });
    expect(text(file.bytes)).toBe('<svg id="b"/>');
    await act(() => result.current.exportImage("svg", 1, c));
    expect(mocks.platform.exportFile).toHaveBeenCalledOnce();
  });

  it("knows the selected image by its id, not by the copy of it a context menu kept", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg edited/>", DEFAULT_SETTINGS));
    const older = { ...a, job: null }; // the same image, from a render before a state update
    await act(() => result.current.exportImage("svg", 1, older));
    expect(text(mocks.platform.exportFile.mock.calls[0][0].bytes)).toBe("<svg edited/>");
  });

  it("names a PNG stem.png at 1× and stem@2x.png at 2×, rendered at that scale", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("png", 1));
    await act(() => result.current.exportImage("png", 2));
    expect(mocks.platform.exportFile.mock.calls.map(([f]) => f.name)).toEqual(["logo.png", "logo@2x.png"]);
    expect(mocks.png).toHaveBeenLastCalledWith("<svg/>", 4, 2, 2);
    expect([...mocks.platform.exportFile.mock.calls[0][0].bytes]).toEqual([1, 2, 3]);
  });

  it("offers Show in Finder only in the app and only when the app is not revealing it already", async () => {
    const { result, rerender } = renderHook(({ s }) => useExports(state(a), a, "<svg/>", s), { initialProps: { s: DEFAULT_SETTINGS } });
    await act(() => result.current.exportImage("svg", 1));
    expect(mocks.success).toHaveBeenCalledWith("Exported logo.svg", expect.objectContaining({ action: expect.objectContaining({ label: "Show in Finder" }) }));
    mocks.success.mock.calls[0][1].action.onClick();
    expect(mocks.platform.reveal).toHaveBeenCalledWith("/Users/t/logo.svg");

    mocks.success.mockClear();
    rerender({ s: { ...DEFAULT_SETTINGS, revealAfterExport: true } });
    await act(() => result.current.exportImage("svg", 1));
    mocks.platform.kind = "web";
    rerender({ s: DEFAULT_SETTINGS });
    await act(() => result.current.exportImage("svg", 1));
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("says why Show in Finder did nothing when the file has gone", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("svg", 1));
    mocks.platform.reveal.mockRejectedValue(new Error("There is no file at /Users/t/logo.svg"));
    await act(async () => mocks.success.mock.calls[0][1].action.onClick());
    expect(mocks.error).toHaveBeenCalledWith("There is no file at /Users/t/logo.svg");
  });

  it("says nothing when the save panel was cancelled", async () => {
    mocks.platform.exportFile.mockResolvedValue(null);
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("svg", 1));
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it("turns a failure into an error toast, for each way out", async () => {
    mocks.platform.exportFile.mockRejectedValue(new Error("disk full"));
    mocks.platform.exportAll.mockRejectedValue(new Error("no folder"));
    mocks.platform.copyText.mockRejectedValue(new Error("clipboard blocked"));
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportImage("svg", 1));
    await act(() => result.current.exportAll());
    await act(() => result.current.copySvg());
    expect(mocks.error.mock.calls.map((c) => c[0])).toEqual(["disk full", "no folder", "clipboard blocked"]);
  });

  it("copies the edited SVG", async () => {
    const { result } = renderHook(() => useExports(state(a), a, "<svg edited/>", DEFAULT_SETTINGS));
    await act(() => result.current.copySvg());
    expect(mocks.platform.copyText).toHaveBeenCalledWith("<svg edited/>");
    expect(mocks.success).toHaveBeenCalledWith("Copied SVG");
  });

  it("Export All writes every traced image, the selected one as edited, and skips untraced ones", async () => {
    const { result } = renderHook(() => useExports(state(a, b, c), a, "<svg edited/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportAll());
    expect(mocks.platform.exportAll).toHaveBeenCalledWith(
      [
        { id: "a", name: "logo.svg", svg: "<svg edited/>" },
        { id: "b", name: "photo.svg", svg: '<svg id="b"/>' },
      ],
      DEFAULT_SETTINGS,
    );
  });

  it("Export All does nothing when no image is traced", async () => {
    const { result } = renderHook(() => useExports(state(c), c, undefined, DEFAULT_SETTINGS));
    await act(() => result.current.exportAll());
    expect(mocks.platform.exportAll).not.toHaveBeenCalled();
  });

  it("tells the library what was exported or copied, by the trace it was, and nothing when the panel was cancelled", async () => {
    const marked = vi.fn();
    const { result } = renderHook(() => useExports(state(a, b, c), a, "<svg edited/>", DEFAULT_SETTINGS, marked));
    await act(() => result.current.exportImage("png", 2, b));
    expect(marked).toHaveBeenLastCalledWith([{ id: "b", key }]);
    await act(() => result.current.copySvg());
    expect(marked).toHaveBeenLastCalledWith([{ id: "a", key }]);
    await act(() => result.current.exportAll());
    expect(marked).toHaveBeenLastCalledWith([{ id: "a", key }, { id: "b", key }]);
    marked.mockClear();
    mocks.platform.exportFile.mockResolvedValue(null);
    mocks.platform.exportAll.mockResolvedValue(null);
    mocks.platform.copyText.mockRejectedValue(new Error("clipboard blocked"));
    await act(() => result.current.exportImage("svg", 1));
    await act(() => result.current.exportAll());
    await act(() => result.current.copySvg());
    expect(marked).not.toHaveBeenCalled();
  });

  it("Export All marks only the images written, and says how many and which ones were not", async () => {
    const marked = vi.fn();
    mocks.platform.exportAll.mockResolvedValue({ written: ["/x/logo.svg"], failed: [{ id: "b", name: "photo.svg", message: "Studi0Trace cannot write to the folder of \u201cphoto.svg\u201d." }] });
    const { result } = renderHook(() => useExports(state(a, b, c), a, "<svg/>", DEFAULT_SETTINGS, marked));
    await act(() => result.current.exportAll());
    expect(marked).toHaveBeenCalledWith([{ id: "a", key }]);
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith("Studi0Trace cannot write to the folder of \u201cphoto.svg\u201d.");
  });

  it("Export All with nothing written marks nothing and says so", async () => {
    const marked = vi.fn();
    mocks.platform.exportAll.mockResolvedValue({ written: [], failed: [{ id: "a", name: "logo.svg", message: "no" }] });
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS, marked));
    await act(() => result.current.exportAll());
    expect(marked).not.toHaveBeenCalled();
    expect(mocks.error).toHaveBeenCalledWith("no");
  });

  it("Export All names every image that failed, once, when several did", async () => {
    mocks.platform.exportAll.mockResolvedValue({ written: [], failed: [{ id: "a", name: "logo.svg", message: "first reason" }, { id: "b", name: "photo.svg", message: "second" }] });
    const { result } = renderHook(() => useExports(state(a, b), a, "<svg/>", DEFAULT_SETTINGS));
    await act(() => result.current.exportAll());
    expect(mocks.error).toHaveBeenCalledWith("2 images could not be exported: \u201clogo.svg\u201d, \u201cphoto.svg\u201d. first reason");
  });

  it("marks the selected image by the trace on screen now, which its bytes came from, not by an older copy's", async () => {
    const marked = vi.fn();
    const { result } = renderHook(() => useExports(state(a), a, "<svg edited/>", DEFAULT_SETTINGS, marked));
    const older = { ...a, shown: "an-earlier-trace" }; // a context menu's copy from before the latest trace took the screen
    await act(() => result.current.exportImage("svg", 1, older));
    expect(marked).toHaveBeenLastCalledWith([{ id: "a", key }]);
  });

  it("ignores an export asked for while a save panel is still open", async () => {
    let close: (path: string | null) => void = () => {};
    mocks.platform.exportFile.mockReturnValue(new Promise((r) => (close = r)));
    const { result } = renderHook(() => useExports(state(a), a, "<svg/>", DEFAULT_SETTINGS));
    let first: Promise<void> = Promise.resolve();
    act(() => {
      first = result.current.exportImage("svg", 1);
    });
    await act(() => result.current.exportImage("png", 2));
    await act(() => result.current.exportAll());
    expect(mocks.platform.exportFile).toHaveBeenCalledOnce();
    expect(mocks.platform.exportAll).not.toHaveBeenCalled();
    close(null);
    await act(() => first);
    await act(() => result.current.exportImage("svg", 1));
    expect(mocks.platform.exportFile).toHaveBeenCalledTimes(2);
  });
});
