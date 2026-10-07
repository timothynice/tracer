import { useCallback, useEffect, useState } from "react";

import { platform } from "@/platform";

/** A platform call that may not exist (an older host, a test's partial mock) fails as a rejection, not a throw. */
const attempt = <T>(f: () => Promise<T>): Promise<T> => Promise.resolve().then(f);

/**
 * Whether an OpenAI key is stored (null while unknown, and in a browser), kept current from every window, with
 * save and remove. The key goes to the app and never comes back.
 */
export function useRedrawKey(): { stored: boolean | null; save: (key: string) => Promise<void>; remove: () => Promise<void> } {
  const [stored, setStored] = useState<boolean | null>(null);
  useEffect(() => {
    if (platform.kind !== "native") return;
    let live = true;
    let off: () => void = () => {};
    attempt(() => platform.redrawKeyStatus()).then(
      (s) => live && setStored(s),
      () => live && setStored(null),
    );
    try {
      off = platform.onRedrawKey((s) => live && setStored(s));
    } catch {
      /* no key events here */
    }
    return () => {
      live = false;
      off();
    };
  }, []);
  const save = useCallback(async (key: string) => {
    await platform.setRedrawKey(key);
    setStored(true);
  }, []);
  const remove = useCallback(async () => {
    await platform.deleteRedrawKey();
    setStored(false);
  }, []);
  return { stored, save, remove };
}
