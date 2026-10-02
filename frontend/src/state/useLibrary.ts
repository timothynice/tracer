import { useSyncExternalStore } from "react";

import type { ImageItem, Library, LibraryState } from "./library";

export function useLibrary(lib: Library): LibraryState {
  return useSyncExternalStore(lib.subscribe, lib.getState, lib.getState);
}

export function useSelected(lib: Library): ImageItem | null {
  const state = useLibrary(lib);
  return state.selected ? (state.items.find((i) => i.image.id === state.selected) ?? null) : null;
}
