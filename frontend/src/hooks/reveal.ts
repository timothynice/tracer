import { toast } from "sonner";

import { platform } from "@/platform";

/** Show a file in Finder; where it cannot be shown (moved, deleted) say so instead of doing nothing. */
export async function revealInFinder(path: string): Promise<void> {
  try {
    await platform.reveal(path);
  } catch (err) {
    toast.error((err as Error).message);
  }
}
