import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { colourText, CONSENT_CHANGES, CONSENT_UPLOAD, edgesText, PRIVACY_SENTENCE, redrawErrorText, redrawFailureText, roughHint, VERDICT_LABEL } from "./redraw";

const drift = (edgeF1: number, deltaE: number) => ({ edgeF1, deltaE, verdict: "large" as const });

describe("redraw words", () => {
  it("words the drift as the spec does", () => {
    expect(edgesText(drift(0.59, 3.8))).toBe("Edges matched: 59 %");
    expect(colourText(drift(0.59, 3.8))).toBe("Colour shift: ΔE 3.8");
    expect(colourText(drift(1, 0.94))).toBe("Colour shift: ΔE 0.9");
    expect(VERDICT_LABEL).toEqual({ close: "Close", noticeable: "Noticeable", large: "Large" });
  });

  it("floors the percentage, so a Noticeable edge F1 never reads as Close's 95 %", () => {
    expect(edgesText(drift(0.946, 3))).toBe("Edges matched: 94 %");
    expect(edgesText(drift(0.9499, 3))).toBe("Edges matched: 94 %");
    expect(edgesText(drift(0.95, 1))).toBe("Edges matched: 95 %");
    expect(edgesText(drift(0.997, 0.9))).toBe("Edges matched: 99 %");
    expect(edgesText(drift(1, 0))).toBe("Edges matched: 100 %");
    // a binary float that sits a hair under the whole percentage still reads as it
    expect(edgesText(drift(0.58 + 0.01, 3))).toBe("Edges matched: 59 %");
    expect(edgesText(drift(0.29 * 3, 3))).toBe("Edges matched: 87 %");
  });

  it("hints only for a rough image, by its reason", () => {
    expect(roughHint(undefined)).toBeNull();
    expect(roughHint({ rough: false, reason: null })).toBeNull();
    expect(roughHint({ rough: true, reason: "small" })).toBe("This image is small — an AI redraw may trace cleaner.");
    expect(roughHint({ rough: true, reason: "doubled" })).toBe("This image is pixel-doubled — an AI redraw may trace cleaner.");
  });

  it("shows a failure in the app's words; a cancel says nothing", () => {
    expect(redrawErrorText(new ApiError("quota", "Your OpenAI account is out of credit or rate limited.", 429))).toBe("Your OpenAI account is out of credit or rate limited.");
    expect(redrawErrorText(new ApiError("cancelled", "The redraw was cancelled", 499))).toBeNull();
  });

  it("takes the words from the code, never from the server's message", () => {
    const secret = "Incorrect API key provided: sk-proj-abcd1234";
    expect(redrawErrorText(new ApiError("invalid_key", secret, 401))).toBe("OpenAI did not accept your API key. Replace it in Settings ▸ AI redraw.");
    expect(redrawErrorText(new ApiError("not_allowed", secret, 403))).toBe(
      "OpenAI did not allow this key to create images. Your organization may need to be verified for image models at platform.openai.com.",
    );
    expect(redrawErrorText(new ApiError("engine_crashed", "The trace worker crashed", 500))).toBe("The redraw stopped unexpectedly.");
    expect(redrawErrorText(new ApiError("refused", secret, 422))).toBe("OpenAI declined to redraw this image under its content policy.");
    expect(redrawErrorText(new ApiError("timeout", secret, 504))).toBe("OpenAI did not answer within 2 minutes. Try again.");
    expect(redrawErrorText(new ApiError("offline", secret, 503))).toBe("Studi0Trace could not reach OpenAI. Check your internet connection.");
    expect(redrawErrorText(new ApiError("bad_reply", secret, 502))).toBe("OpenAI's reply held no usable image. Try again.");
    expect(redrawErrorText(new ApiError("too_large", secret, 413))).toBe("This image is too large to send to OpenAI (50 MB at most).");
    expect(redrawErrorText(new ApiError("no_key", secret, 401))).toBe("Add your OpenAI API key to use AI redraw.");
    expect(redrawErrorText(new ApiError("something_new", secret, 500))).toBe("The redraw failed.");
  });

  it("says what is uploaded, to whom, on whose bill, and that it is checked first", () => {
    expect(PRIVACY_SENTENCE).toBe("Your images stay on your Mac: nothing is uploaded unless you choose AI redraw, which sends that one image to OpenAI with your own API key.");
    expect(CONSENT_UPLOAD).toContain("uploaded to OpenAI with your API key and billed to your OpenAI account");
    expect(CONSENT_CHANGES).toContain("may change shapes, spacing and colours");
  });

  it("words any failed redraw action, an ApiError by code and anything else plainly; a cancel is silent", () => {
    expect(redrawFailureText(new ApiError("quota", "sk-secret", 429))).toBe("Your OpenAI account is out of credit or rate limited.");
    expect(redrawFailureText(new ApiError("cancelled", "x", 499))).toBeNull();
    expect(redrawFailureText(new Error("sk-secret"))).toBe("The redraw failed.");
    expect(redrawFailureText("boom")).toBe("The redraw failed.");
  });

  it("the README says the same sentence as the consent sheet", () => {
    expect(readFileSync("../README.md", "utf8")).toContain(PRIVACY_SENTENCE);
  });
});
