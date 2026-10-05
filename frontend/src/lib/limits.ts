import type { OpenFailure } from "@/platform/types";

/** What the core admits: a side of at most this many pixels, a file of at most this many megabytes. */
export const MAX_SIDE = 2048;
export const MAX_MB = 20;

/**
 * What to say of a file that could not be opened. The core words its refusal of an over-long side as an area
 * ("Image exceeds the 2048x2048 pixel limit"), which reads wrongly for a 4000×200 image; its message is a contract
 * with the Python, so the app says it in its own words and takes the numbers from the message where it has them.
 */
export function failureText(f: OpenFailure): string {
  const { code, message } = f.error;
  if (code === "too_many_pixels") {
    // an area limit ("16 megapixel") is not a side limit: the core's own words are right for it
    if (/megapixel/i.test(message)) return message;
    const side = /(\d+)x\d+/.exec(message)?.[1] ?? MAX_SIDE;
    return `${f.name} is larger than ${side} px on a side.`;
  }
  if (code === "too_large") {
    const mb = /(\d+)\s*MB/i.exec(message)?.[1] ?? MAX_MB;
    return `${f.name} is larger than ${mb} MB.`;
  }
  return message;
}
