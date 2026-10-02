import type { MenuCommand } from "@/platform/types";

/** What the menu bar can ask of the workspace, plus the two the Mac app's Rust side handles itself. */
export type Command = MenuCommand | "open" | "settings";

type Keys = Pick<KeyboardEvent, "code" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">;

const MODES: Record<string, Command> = { Digit1: "mode-split", Digit2: "mode-side", Digit3: "mode-overlay", Digit4: "mode-vector" };

/**
 * The menu's accelerators, for a browser, where no menu bar owns them. By `code`, not `key`: Option changes the
 * character a key types (⌥E is a dead key), not the key.
 */
export function commandForKey(e: Keys): Command | null {
  if (!e.metaKey) return null;
  const { code, shiftKey: shift, altKey: alt, ctrlKey: ctrl } = e;
  const plain = !shift && !alt && !ctrl;
  if (code in MODES) return plain ? MODES[code] : null;
  switch (code) {
    case "KeyO":
      return plain ? "open" : null;
    case "KeyE":
      return plain ? "export-svg" : shift && !alt && !ctrl ? "export-png-2" : alt && !shift && !ctrl ? "export-all" : null;
    case "KeyC":
      return shift && !alt && !ctrl ? "copy-svg" : null;
    case "KeyR":
      return alt && !shift && !ctrl ? "reveal" : null;
    case "Equal":
      return !alt && !ctrl ? "zoom-in" : null;
    case "Minus":
      return plain ? "zoom-out" : null;
    case "Digit0":
      return plain ? "zoom-actual" : null;
    case "Digit9":
      return plain ? "zoom-fit" : null;
    case "KeyS":
      return ctrl && !alt && !shift ? "toggle-sidebar" : null;
    case "KeyI":
      return alt && !ctrl && !shift ? "toggle-inspector" : null;
    case "Enter":
      return plain ? "generate" : null;
    case "Period":
      return plain ? "cancel" : null;
    case "Backspace":
      return plain ? "remove" : null;
    case "Comma":
      return plain ? "settings" : null;
    default:
      return null;
  }
}

export const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
