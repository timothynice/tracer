import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VEXEL, VEXEL_PRESETS } from "@/test/server";

const hooks = vi.hoisted(() => ({
  role: "main" as "main" | "settings",
  load: null as null | (() => Promise<unknown>),
  listeners: new Set<(s: unknown) => void>(),
  saved: [] as unknown[],
}));

vi.mock("@/platform", async () => {
  const types = await vi.importActual<typeof import("@/platform/types")>("@/platform/types");
  const platform = {
    kind: "native",
    engines: async () => [VEXEL],
    presets: async () => VEXEL_PRESETS,
    pickImages: async () => [],
    openFiles: async () => [],
    openPaths: async () => [],
    closeImage: vi.fn(),
    vectorize: vi.fn(() => new Promise(() => {})),
    exportFile: vi.fn(async () => null),
    exportAll: vi.fn(async () => null),
    copyText: vi.fn(async () => {}),
    reveal: vi.fn(async () => {}),
    loadSettings: () => hooks.load!(),
    saveSettings: async (s: unknown) => {
      hooks.saved.push(s);
      return s;
    },
    onSettings: (cb: (s: unknown) => void) => {
      hooks.listeners.add(cb);
      return () => hooks.listeners.delete(cb);
    },
    onMenu: () => () => {},
    onOpenPaths: () => () => {},
    onOpenFailures: () => () => {},
    onDragState: () => () => {},
    setMenuState: () => {},
    confirmClear: vi.fn(async () => true),
    openSettingsWindow: () => true,
    windowRole: () => hooks.role,
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");
const { DEFAULT_SETTINGS } = await import("@/platform/types");

const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );

afterEach(() => {
  hooks.role = "main";
  hooks.saved = [];
  hooks.listeners.clear();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("the Settings window", () => {
  it("shows the saved settings, saves each change at once and follows a change made elsewhere", async () => {
    hooks.role = "settings";
    hooks.load = async () => ({ ...DEFAULT_SETTINGS, appearance: "dark" });
    mount();
    await waitFor(() => expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true"));
    expect(document.documentElement).toHaveClass("dark");
    fireEvent.click(screen.getByRole("switch", { name: "Show in Finder after export" }));
    expect(hooks.saved.at(-1)).toMatchObject({ appearance: "dark", revealAfterExport: true });
    act(() => hooks.listeners.forEach((l) => l({ ...DEFAULT_SETTINGS, appearance: "light", exportTo: "beside" })));
    expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Next to the original" })).toHaveAttribute("aria-checked", "true");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("studi0trace.appearance")).toBe("light");
  });
});

describe("loading the settings", () => {
  it("does not let a load that resolves late overwrite a change that already arrived", async () => {
    let resolve!: (s: unknown) => void;
    hooks.load = () => new Promise((r) => (resolve = r));
    mount();
    await screen.findByText("Drop images here");
    act(() => hooks.listeners.forEach((l) => l({ ...DEFAULT_SETTINGS, appearance: "dark" })));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    await act(async () => resolve({ ...DEFAULT_SETTINGS, appearance: "light" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("studi0trace.appearance")).toBe("dark");
  });

  it("falls back to the defaults when the load is rejected, with no unhandled rejection", async () => {
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    hooks.load = () => Promise.reject(new Error("store unreadable"));
    mount();
    await screen.findByText("Drop images here");
    await new Promise((r) => setTimeout(r, 20));
    expect(unhandled).not.toHaveBeenCalled();
    process.off("unhandledRejection", unhandled);
  });
});
