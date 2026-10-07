import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VEXEL, VEXEL_PRESETS } from "@/test/server";

// a style sits under "Choose a style", folded while Auto is the pick
const pickStyle = (name: string) => {
  const toggle = screen.getByRole("button", { name: "Choose a style" });
  if (toggle.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("radio", { name }));
};

const hooks = vi.hoisted(() => ({ menu: null as null | ((c: string) => void), opens: null as null | ((p: string[]) => void), failures: null as null | ((f: unknown[]) => void), states: [] as unknown[] }));

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
    loadSettings: async () => types.DEFAULT_SETTINGS,
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
    onOpenFailures: (cb: (f: unknown[]) => void) => {
      hooks.failures = cb;
      return () => {};
    },
    onDragState: () => () => {},
    setMenuState: (s: unknown) => hooks.states.push(s),
    confirmClear: vi.fn(async () => true),
    openSettingsWindow: () => true,
    windowRole: () => "main",
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");
const { platform } = await import("@/platform");

describe("the Mac app's wiring", () => {
  it("opens what Finder sends, follows the menu, and keeps the menu bar told", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    expect(hooks.states.at(-1)).toMatchObject({ hasItems: false, hasImage: false, tracing: false, anyTracing: false, unexported: 0, sidebar: true, inspector: true, mode: "split" });

    act(() => hooks.opens!(["/pics/logo.png"]));
    expect(await screen.findByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ hasItems: true, hasImage: true, hasPath: true, hasVector: false }));

    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: true, anyTracing: true }));
    act(() => hooks.menu!("mode-overlay"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ mode: "overlay" }));
    act(() => hooks.menu!("toggle-inspector"));
    expect(screen.queryByRole("complementary", { name: "Vectorize" })).toBeNull();
    act(() => hooks.menu!("cancel"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: false, anyTracing: false }));
  });

  it("tells the menu bar how many traced images are unexported, and shows the app's open failures", async () => {
    vi.mocked(platform.vectorize).mockImplementationOnce(async () => ({
      success: true,
      image_id: "x",
      width: 64,
      height: 64,
      results: { vexel: { svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M0 0H8V8Z" fill="#000"/></svg>', elapsed_ms: 5000, stats: {} } },
      parameters_used: { vexel: {} },
      auto: null,
    }));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    act(() => hooks.opens!(["/pics/mark.png"]));
    await screen.findByRole("option", { name: /mark\.png/ });
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ hasVector: true, unexported: 1, anyTracing: false }));
    act(() => hooks.menu!("copy-svg"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ unexported: 0 }));

    await waitFor(() => expect(hooks.failures).not.toBeNull());
    act(() => hooks.failures!([{ name: "Dropped items", path: null, error: { code: "nothing_to_open", message: "Nothing to open: drop image files or a folder of them." } }]));
    expect(await screen.findByText("Nothing to open: drop image files or a folder of them.")).toBeInTheDocument();
  });

  it("Clear All asks first when a traced vector would be lost, from the menu and from the sidebar, and not otherwise", async () => {
    const traced = () =>
      vi.mocked(platform.vectorize).mockImplementationOnce(async () => ({
        success: true,
        image_id: "x",
        width: 64,
        height: 64,
        results: { vexel: { svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M0 0H8V8Z" fill="#000"/></svg>', elapsed_ms: 5000, stats: {} } },
        parameters_used: { vexel: {} },
        auto: null,
      }));
    traced();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    act(() => hooks.opens!(["/pics/mark.png"]));
    await screen.findByRole("option", { name: /mark\.png/ });
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ unexported: 1 }));
    const confirm = vi.mocked(platform.confirmClear);
    confirm.mockClear();

    // Cancel keeps everything, on both roads
    confirm.mockResolvedValueOnce(false);
    act(() => hooks.menu!("clear"));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(1));
    confirm.mockResolvedValueOnce(false);
    fireEvent.click(screen.getAllByRole("button", { name: /Clear All/ }).at(-1)!);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("option", { name: /mark\.png/ })).toBeInTheDocument();

    // Clear All clears
    confirm.mockResolvedValueOnce(true);
    fireEvent.click(screen.getAllByRole("button", { name: /Clear All/ }).at(-1)!);
    await waitFor(() => expect(screen.queryByRole("option", { name: /mark\.png/ })).toBeNull());

    // an exported vector is nothing to lose: no question
    traced();
    act(() => hooks.opens!(["/pics/other.png"]));
    await screen.findByRole("option", { name: /other\.png/ });
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ unexported: 1 }));
    act(() => hooks.menu!("copy-svg"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ unexported: 0 }));
    confirm.mockClear();
    act(() => hooks.menu!("clear"));
    await waitFor(() => expect(screen.queryByRole("option", { name: /other\.png/ })).toBeNull());
    expect(confirm).not.toHaveBeenCalled();
  });

  it("a trace for other settings runs in the background: the cached trace shown is not busy, the menu and quit know", async () => {
    const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M0 0H8V8Z" fill="#000"/></svg>';
    const answer = (svg: string) => ({ success: true, image_id: "x", width: 64, height: 64, results: { vexel: { svg, elapsed_ms: 5000, stats: {} } }, parameters_used: { vexel: {} }, auto: null });
    let finishAuto: (r: unknown) => void = () => {};
    vi.mocked(platform.vectorize)
      .mockImplementationOnce(async () => answer(SVG))
      .mockImplementationOnce(() => new Promise((resolve) => (finishAuto = resolve)) as never);
    vi.mocked(platform.vectorize).mockClear();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    act(() => hooks.opens!(["/pics/bg.png"]));
    await screen.findByRole("option", { name: /bg\.png/ });
    pickStyle("Balanced");
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ hasVector: true, tracing: false }));
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: true, anyTracing: true }));
    expect(screen.getAllByText("Queued…").length).toBeGreaterThan(0); // the pill and the button, for the job on screen

    // back to the cached preset while Auto still runs
    pickStyle("Balanced");
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: false, anyTracing: true }));
    expect(screen.queryByText("Queued…")).toBeNull();
    expect(screen.queryByText(/Tracing…/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Cancel/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();

    // Auto lands: its answer is kept, and Auto shows it at once
    await act(async () => finishAuto(answer("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'/>")));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ anyTracing: false }));
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    expect(screen.getByRole("button", { name: "Up to date" })).toBeDisabled();
    expect(platform.vectorize).toHaveBeenCalledTimes(2);
  });

  it("keeps the web's context menu out of the app, but not out of text fields", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    const onPage = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    document.body.dispatchEvent(onPage);
    expect(onPage.defaultPrevented).toBe(true);
    const field = document.createElement("input");
    document.body.append(field);
    const inField = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    field.dispatchEvent(inField);
    expect(inField.defaultPrevented).toBe(false);
    field.remove();
  });

  async function withImageAndAdvanced() {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    act(() => hooks.opens!(["/pics/logo.png"]));
    await screen.findByRole("option", { name: /logo\.png/ });
    fireEvent.click(await screen.findByRole("button", { name: "Advanced Options" }));
    fireEvent.click(await screen.findByRole("button", { name: /Shapes/ }));
  }

  it("leaves ⌘⌫ to a focused text field: the menu's Remove Image does not remove the image", async () => {
    await withImageAndAdvanced();
    const field = screen.getByRole("spinbutton", { name: "Smallest shape" });
    field.focus();
    act(() => hooks.menu!("remove"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
    field.blur();
    act(() => hooks.menu!("remove"));
    await waitFor(() => expect(screen.queryByRole("option", { name: /logo\.png/ })).toBeNull());
  });

  it("commits a number being typed before the menu's Generate traces with it", async () => {
    await withImageAndAdvanced();
    vi.mocked(platform.vectorize).mockClear();
    const field = screen.getByRole("spinbutton", { name: "Detail" });
    field.focus();
    fireEvent.change(field, { target: { value: "13" } }); // typed, not yet committed (that happens on blur)
    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(platform.vectorize).toHaveBeenCalledOnce());
    expect(vi.mocked(platform.vectorize).mock.calls[0][0].parameters).toMatchObject({ detail: 13 });
  });
});
