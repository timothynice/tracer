import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VEXEL, VEXEL_PRESETS } from "@/test/server";

const hooks = vi.hoisted(() => ({ menu: null as null | ((c: string) => void), opens: null as null | ((p: string[]) => void), failures: null as null | ((f: unknown[]) => void), error: vi.fn() }));
vi.mock("sonner", async (orig) => ({ ...(await orig<typeof import("sonner")>()), toast: Object.assign(vi.fn(), { error: hooks.error, success: vi.fn() }) }));

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
    setMenuState: () => {},
    openSettingsWindow: () => true,
    windowRole: () => "main",
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");
const { platform } = await import("@/platform");

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );
}

describe("what goes wrong after an open", () => {
  it("says so when Show in Finder cannot find the original, from the menu and from the image's menu", async () => {
    vi.mocked(platform.reveal).mockRejectedValue(new Error("The original is no longer at /pics/logo.png"));
    mount();
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    act(() => hooks.opens!(["/pics/logo.png"]));
    await screen.findByRole("option", { name: /logo\.png/ });
    act(() => hooks.menu!("reveal"));
    await waitFor(() => expect(hooks.error).toHaveBeenCalledWith("The original is no longer at /pics/logo.png"));
  });

  it("says so when Downscale fails, and keeps the failure card", async () => {
    vi.mocked(platform.openPaths).mockRejectedValueOnce(new Error("sips could not read it"));
    mount();
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.failures).not.toBeNull());
    act(() => hooks.failures!([{ name: "huge.png", path: "/pics/huge.png", error: { code: "too_many_pixels", message: "Image exceeds the 2048x2048 pixel limit" } }]));
    expect(await screen.findByText("huge.png is larger than 2048 px on a side.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Downscale to 2048 px" }));
    await waitFor(() => expect(hooks.error).toHaveBeenCalledWith("sips could not read it"));
    expect(screen.getByText("huge.png is larger than 2048 px on a side.")).toBeInTheDocument();
  });
});
