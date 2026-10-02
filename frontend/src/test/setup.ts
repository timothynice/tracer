import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// Node 25+ has a `localStorage` global of its own (backed by --localstorage-file, and useless without it). It
// shadows jsdom's on globalThis; the tests mean jsdom's. Defined from jsdom's window, never reading Node's getter
// (which warns).
const jsdomWindow = (globalThis as { jsdom?: { window: Window } }).jsdom?.window ?? window;
Object.defineProperty(globalThis, "localStorage", { value: jsdomWindow.localStorage, configurable: true, writable: true });

// jsdom lacks these; components only need them to exist.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}
if (!("ResizeObserver" in window)) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => {};
}
// jsdom 26 has no PointerEvent, so testing-library falls back to a bare Event and drops button, clientX and the
// rest of the init. A MouseEvent that also carries pointerId is enough for the components' handlers.
if (!("PointerEvent" in window)) {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? "mouse";
    }
  }
  (window as unknown as { PointerEvent: unknown }).PointerEvent = PointerEventPolyfill;
}
