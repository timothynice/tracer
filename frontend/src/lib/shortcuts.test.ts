import { describe, expect, it } from "vitest";

import { commandForKey } from "./shortcuts";

const key = (code: string, mods: { shift?: boolean; alt?: boolean; ctrl?: boolean; meta?: boolean } = {}) => ({
  code,
  key: "",
  metaKey: mods.meta ?? true,
  ctrlKey: !!mods.ctrl,
  shiftKey: !!mods.shift,
  altKey: !!mods.alt,
});

describe("commandForKey", () => {
  it("knows the menu's accelerators", () => {
    const table: [ReturnType<typeof key>, string | null][] = [
      [key("KeyO"), "open"],
      [key("KeyE"), "export-svg"],
      [key("KeyE", { shift: true }), "export-png-2"],
      [key("KeyE", { alt: true }), "export-all"],
      [key("KeyC", { shift: true }), "copy-svg"],
      [key("KeyR", { alt: true }), "reveal"],
      [key("Equal"), "zoom-in"],
      [key("Equal", { shift: true }), "zoom-in"],
      [key("Minus"), "zoom-out"],
      [key("Digit0"), "zoom-actual"],
      [key("Digit9"), "zoom-fit"],
      [key("Digit1"), "mode-split"],
      [key("Digit2"), "mode-side"],
      [key("Digit3"), "mode-overlay"],
      [key("Digit4"), "mode-vector"],
      [key("KeyS", { ctrl: true }), "toggle-sidebar"],
      [key("KeyI", { alt: true }), "toggle-inspector"],
      [key("Enter"), "generate"],
      [key("Period"), "cancel"],
      [key("Backspace"), "remove"],
      [key("Comma"), "settings"],
    ];
    for (const [e, want] of table) expect([e.code, e.shiftKey, e.altKey, e.ctrlKey, commandForKey(e)]).toEqual([e.code, e.shiftKey, e.altKey, e.ctrlKey, want]);
  });

  it("ignores what is not one of them", () => {
    expect(commandForKey(key("KeyO", { meta: false }))).toBeNull();
    expect(commandForKey(key("KeyC"))).toBeNull(); // plain ⌘C is the system's Copy
    expect(commandForKey(key("KeyS"))).toBeNull();
    expect(commandForKey(key("KeyQ"))).toBeNull();
  });
});
