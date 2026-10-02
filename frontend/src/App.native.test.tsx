import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VEXEL, VEXEL_PRESETS } from "@/test/server";

const hooks = vi.hoisted(() => ({ menu: null as null | ((c: string) => void), opens: null as null | ((p: string[]) => void), states: [] as unknown[] }));

vi.mock("@/platform", async () => {
  const types = await vi.importActual<typeof import("@/platform/types")>("@/platform/types");
  const platform = {
    kind: "native",
    health: async () => ({ status: "ok", version: "0.3.0", engines: ["vexel"] }),
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
    onDragState: () => () => {},
    setMenuState: (s: unknown) => hooks.states.push(s),
    openSettingsWindow: () => true,
    windowRole: () => "main",
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");

describe("the Mac app's wiring", () => {
  it("opens what Finder sends, follows the menu, and keeps the menu bar told", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    expect(hooks.states.at(-1)).toMatchObject({ hasItems: false, hasImage: false, tracing: false, sidebar: true, inspector: true, mode: "split" });

    act(() => hooks.opens!(["/pics/logo.png"]));
    expect(await screen.findByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ hasItems: true, hasImage: true, hasPath: true, hasVector: false }));

    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: true }));
    act(() => hooks.menu!("mode-overlay"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ mode: "overlay" }));
    act(() => hooks.menu!("toggle-inspector"));
    expect(screen.queryByRole("complementary", { name: "Vectorize" })).toBeNull();
    act(() => hooks.menu!("cancel"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: false }));
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
});
