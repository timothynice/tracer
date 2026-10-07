import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { PRIVACY_SENTENCE } from "@/lib/redraw";
import { RedrawConsentSheet } from "./RedrawConsentSheet";

describe("RedrawConsentSheet", () => {
  it("says what is uploaded, to whom and on whose bill, and stores the pasted key", async () => {
    const onSave = vi.fn(async () => {});
    render(<RedrawConsentSheet open onOpenChange={vi.fn()} onSave={onSave} />);
    const dialog = screen.getByRole("dialog", { name: "Redraw with AI" });
    expect(dialog).toHaveTextContent("uploaded to OpenAI with your API key and billed to your OpenAI account");
    expect(dialog).toHaveTextContent("may change shapes, spacing and colours");
    expect(dialog).toHaveTextContent(PRIVACY_SENTENCE);
    const save = screen.getByRole("button", { name: "Save Key and Redraw" });
    expect(save).toBeDisabled();
    const field = screen.getByLabelText("OpenAI API key");
    expect(field).toHaveAttribute("type", "password");
    fireEvent.change(field, { target: { value: "  sk-test-1  " } });
    fireEvent.click(save);
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("sk-test-1"));
  });

  it("shows why a key was refused, and keeps the sheet open", async () => {
    const onSave = vi.fn(async () => {
      throw new ApiError("bad_request", "That does not look like an OpenAI API key.", 400);
    });
    render(<RedrawConsentSheet open onOpenChange={vi.fn()} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: "nope nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Key and Redraw" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That does not look like an OpenAI API key.");
    expect(screen.getByRole("dialog", { name: "Redraw with AI" })).toBeInTheDocument();
  });
});
