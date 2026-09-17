import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { Dropzone, validateFile } from "./Dropzone";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
import { toast } from "sonner";

const png = () => new File([new Uint8Array(8)], "a.png", { type: "image/png" });

test("validateFile", () => {
  expect(validateFile(png())).toBeNull();
  expect(validateFile(new File(["x"], "a.txt", { type: "text/plain" }))).toMatch(/supported image/);
  const big = new File([new Uint8Array(1)], "b.png", { type: "image/png" });
  Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
  expect(validateFile(big)).toMatch(/limit is 20 MB/);
});

test("accepts a dropped image", () => {
  const onFile = vi.fn();
  render(<Dropzone onFile={onFile} />);
  const zone = screen.getByRole("button", { name: /upload an image/i });
  fireEvent.drop(zone, { dataTransfer: { files: [png()] } });
  expect(onFile).toHaveBeenCalledTimes(1);
  expect(onFile.mock.calls[0][0].name).toBe("a.png");
});

test("accepts a file from the picker and rejects wrong types with a toast", async () => {
  const onFile = vi.fn();
  render(<Dropzone onFile={onFile} />);
  const input = screen.getByTestId("file-input") as HTMLInputElement;
  await userEvent.upload(input, png());
  expect(onFile).toHaveBeenCalledTimes(1);
  await userEvent.upload(input, new File(["x"], "a.txt", { type: "text/plain" }), { applyAccept: false });
  expect(onFile).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalled();
});

test("accepts a pasted image", () => {
  const onFile = vi.fn();
  render(<Dropzone onFile={onFile} />);
  const file = png();
  const event = new Event("paste", { bubbles: true, cancelable: true }) as ClipboardEvent;
  Object.defineProperty(event, "clipboardData", { value: { items: [{ kind: "file", type: "image/png", getAsFile: () => file }] } });
  document.dispatchEvent(event);
  expect(onFile).toHaveBeenCalledWith(file);
});

test("does nothing while disabled", () => {
  const onFile = vi.fn();
  render(<Dropzone onFile={onFile} disabled disabledReason="Waking server…" />);
  expect(screen.getByText("Waking server…")).toBeInTheDocument();
  fireEvent.drop(screen.getByRole("button"), { dataTransfer: { files: [png()] } });
  expect(onFile).not.toHaveBeenCalled();
});
