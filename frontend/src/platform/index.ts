import { isTauri } from "@tauri-apps/api/core";

import { nativePlatform } from "./native";
import type { Platform } from "./types";
import { webPlatform } from "./web";

export * from "./types";

/** The platform this page runs on, chosen once. */
export const platform: Platform = isTauri() ? nativePlatform() : webPlatform();
