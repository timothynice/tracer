import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { applyTheme, watchSystemTheme } from "@/lib/theme";
import { DEFAULT_SETTINGS, platform, type Settings } from "@/platform";

/** The pre-paint script in index.html reads this first: the Mac app has no synchronous access to its store. */
export const APPEARANCE_KEY = "studi0trace.appearance";

/**
 * The settings of this window: loaded once, kept current from every save in every window, with the appearance
 * applied (and mirrored for the next launch's first paint) as it changes. `change` saves at once.
 */
export function useSettings(): { settings: Settings; change: (next: Settings) => void } {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [ready, setReady] = useState(false);
  // a save that reached this window before the load answered is newer than the load
  const arrived = useRef(false);
  useEffect(() => {
    const off = platform.onSettings((s) => {
      arrived.current = true;
      setSettings(s);
      setReady(true);
    });
    platform.loadSettings().then(
      (s) => {
        if (!arrived.current) setSettings(s);
        setReady(true);
      },
      () => setReady(true), // unreadable: the defaults
    );
    return off;
  }, []);
  const { appearance } = settings;
  useEffect(() => {
    // before the load answers the page keeps the appearance the pre-paint script gave it
    if (!ready) return;
    applyTheme(appearance);
    try {
      localStorage.setItem(APPEARANCE_KEY, appearance);
    } catch {
      /* a convenience for the first paint, nothing more */
    }
    return watchSystemTheme(() => appearance);
  }, [ready, appearance]);
  const change = useCallback((next: Settings) => {
    arrived.current = true;
    setSettings(next);
    setReady(true);
    platform.saveSettings(next).catch((err: Error) => toast.error(`Settings could not be saved: ${err.message}`));
  }, []);
  return { settings, change };
}
