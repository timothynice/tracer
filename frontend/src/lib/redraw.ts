/** AI redraw's words: the drift, the verdicts, the hint, the phases, the failures and the consent. */
import { ApiError } from "./api";
import type { Drift, DriftVerdict, RedrawPhase, Roughness } from "@/platform/types";

/** The README's sentence, and the consent sheet's. */
export const PRIVACY_SENTENCE = "Your images stay on your Mac: nothing is uploaded unless you choose AI redraw, which sends that one image to OpenAI with your own API key.";
export const CONSENT_UPLOAD = "The image is uploaded to OpenAI with your API key and billed to your OpenAI account.";
export const CONSENT_CHANGES = "The redraw may change shapes, spacing and colours. You check it against the original before it is used.";

export const VERDICT_LABEL: Record<DriftVerdict, string> = { close: "Close", noticeable: "Noticeable", large: "Large" };
export const VERDICT_NOTE: Record<DriftVerdict, string> = {
  close: "The redraw keeps the drawing.",
  noticeable: "Some shapes or colours moved. Compare before you use it.",
  large: "The redraw is a different drawing. Check it closely.",
};

/** What the app is doing, after "Redrawing with AI…". */
export const PHASE_TEXT: Record<RedrawPhase, string> = {
  uploading: "Uploading the image",
  drawing: "OpenAI is drawing (10–60 s)",
  checking: "Measuring the drift",
  done: "Measuring the drift",
  failed: "",
};

/** Floored, never rounded: an edge F1 of 0.946 is a Noticeable verdict and must not read as the 95 % that is Close.
 *  The nudge keeps a binary float a hair under a whole percentage (0.58 + 0.01) from losing one. */
export const edgesText = (d: Drift) => `Edges matched: ${Math.floor(d.edgeF1 * 100 + 1e-9)} %`;
export const colourText = (d: Drift) => `Colour shift: ΔE ${d.deltaE.toFixed(1)}`;

/** The inspector's quiet hint for a rough image; null for any other. */
export function roughHint(r: Roughness | undefined): string | null {
  if (!r?.rough) return null;
  return r.reason === "doubled" ? "This image is pixel-doubled — an AI redraw may trace cleaner." : "This image is small — an AI redraw may trace cleaner.";
}

/** A redraw failure's words by its code: the same as the Rust side's table, since a server message may quote the
 *  key (a 401's does) and the engine's own for a crash speaks of a trace. */
const ERROR_WORDS: Record<string, string> = {
  no_key: "Add your OpenAI API key to use AI redraw.",
  invalid_key: "OpenAI did not accept your API key. Replace it in Settings ▸ AI redraw.",
  quota: "Your OpenAI account is out of credit or rate limited.",
  not_allowed: "OpenAI did not allow this key to create images. Your organization may need to be verified for image models at platform.openai.com.",
  refused: "OpenAI declined to redraw this image under its content policy.",
  timeout: "OpenAI did not answer within 4 minutes. Try again.",
  offline: "Studi0Trace could not reach OpenAI. Check your internet connection.",
  bad_reply: "OpenAI's reply held no usable image. Try again.",
  too_large: "This image is too large to send to OpenAI (50 MB at most).",
  keychain: "macOS did not let Studi0Trace read the key. Allow access in the prompt, or remove and add the key again in Settings.",
  engine_crashed: "The redraw stopped unexpectedly.",
};

/** A failed redraw in the app's own words, chosen by code and never from the message; a cancel says nothing. */
export function redrawErrorText(err: ApiError): string | null {
  if (err.code === "cancelled") return null;
  return ERROR_WORDS[err.code] ?? "The redraw failed.";
}

/** Any failure of a redraw action (accept, revert, a redraw that threw) in the app's words: an `ApiError` by its code,
 *  anything else as a plain failure; null for a cancel. */
export function redrawFailureText(err: unknown): string | null {
  return err instanceof ApiError ? redrawErrorText(err) : "The redraw failed.";
}

/** A key that could not be saved or removed, by code: the app's own wording for a key it refused to take (`bad_request`,
 *  from its check of the pasted text, which never repeats the key); the keychain's and anything else's in plain words,
 *  never the message (an OS reason, or a crash that speaks of a trace). */
export function keyErrorText(err: unknown, verb: "save" | "remove"): string {
  const code = err instanceof ApiError ? err.code : null;
  if (code === "bad_request" && err instanceof ApiError) return err.message;
  if (code === "keychain") return `The keychain would not ${verb === "save" ? "store" : "remove"} the key.`;
  return `Something went wrong ${verb === "save" ? "saving" : "removing"} the key.`;
}
