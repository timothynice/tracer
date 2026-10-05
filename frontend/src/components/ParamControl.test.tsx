import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ParamControl } from "./ParamControl";
import { ParamPanel } from "./ParamPanel";
import { specsFor } from "@/lib/schema";
import { ENGINES } from "@/test/server";

const specs = specsFor(ENGINES[0]);
const spec = (name: string) => specs.find((s) => s.name === name)!;

test("slider renders label, number field and unit, and commits typed values", async () => {
  const onChange = vi.fn();
  render(<ParamControl spec={{ ...spec("threshold"), unit: "px" }} value={128} onChange={onChange} />);
  expect(screen.getByText("Threshold")).toBeInTheDocument();
  expect(screen.getByText("px")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Threshold" })).toHaveAttribute("aria-valuenow", "128");
  const input = screen.getByRole("spinbutton") as HTMLInputElement;
  await userEvent.clear(input);
  await userEvent.type(input, "200{enter}");
  expect(onChange).toHaveBeenLastCalledWith(200);
});

test("a number field left without a change commits nothing", async () => {
  const onChange = vi.fn();
  render(<ParamControl spec={spec("threshold")} value={128} onChange={onChange} />);
  const input = screen.getByRole("spinbutton") as HTMLInputElement;
  await userEvent.click(input);
  await userEvent.tab();
  await userEvent.click(input);
  await userEvent.keyboard("{Enter}");
  expect(onChange).not.toHaveBeenCalled();
});

test("an emptied or non-numeric number field goes back to its value", async () => {
  const onChange = vi.fn();
  render(<ParamControl spec={spec("threshold")} value={128} onChange={onChange} />);
  const input = screen.getByRole("spinbutton") as HTMLInputElement;
  await userEvent.clear(input);
  await userEvent.tab();
  expect(onChange).not.toHaveBeenCalled();
  expect(input.value).toBe("128");
  await userEvent.clear(input);
  await userEvent.type(input, "e");
  await userEvent.tab();
  expect(onChange).not.toHaveBeenCalled();
  expect(input.value).toBe("128");
});

test("a typed value past the range that clamps to the current value is not a change, and the field shows the value", async () => {
  const onChange = vi.fn();
  const s = spec("threshold");
  render(<ParamControl spec={s} value={s.max} onChange={onChange} />);
  const input = screen.getByRole("spinbutton") as HTMLInputElement;
  await userEvent.clear(input);
  await userEvent.type(input, `${(s.max ?? 0) + 500}`);
  await userEvent.tab();
  expect(onChange).not.toHaveBeenCalled();
  expect(input.value).toBe(String(s.max));
});

test("toggle flips the boolean", async () => {
  const onChange = vi.fn();
  render(<ParamControl spec={spec("invert")} value={false} onChange={onChange} />);
  await userEvent.click(screen.getByRole("switch", { name: "Invert" }));
  expect(onChange).toHaveBeenCalledWith(true);
});

test("select shows the current option", () => {
  render(<ParamControl spec={spec("turnpolicy")} value="majority" onChange={() => {}} />);
  expect(screen.getByRole("combobox", { name: "Turn policy" })).toHaveTextContent("majority");
});

test("panel groups by ui.group into drawers and counts what changed", async () => {
  const values = { threshold: 10, invert: true, turnpolicy: "black", alphamax: 0.5 };
  render(<ParamPanel engine="potrace" specs={specs} values={values} onChange={() => {}} />);
  expect(screen.getByRole("region", { name: "Bitmap" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Curves" })).toBeInTheDocument();
  // Drawers start closed; the header carries how many values differ from default.
  const bitmap = screen.getByRole("button", { name: /^Bitmap/ });
  expect(bitmap).toHaveAttribute("aria-expanded", "false");
  expect(bitmap).toHaveTextContent("2"); // threshold and invert
  expect(screen.queryByRole("spinbutton", { name: "Threshold" })).not.toBeInTheDocument();
  await userEvent.click(bitmap);
  expect(bitmap).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("spinbutton", { name: "Threshold" })).toBeInTheDocument();
});

test("there is no reset button: re-picking the active preset is the reset", () => {
  render(<ParamPanel engine="potrace" specs={specs} values={{ threshold: 10 }} onChange={() => {}} />);
  expect(screen.queryByRole("button", { name: /reset/i })).not.toBeInTheDocument();
});

test("a drawer holding an invalid field opens itself", () => {
  render(<ParamPanel engine="potrace" specs={specs} values={ENGINES[0].defaults} onChange={() => {}} invalidField="threshold" />);
  expect(screen.getByRole("button", { name: /^Bitmap/ })).toHaveAttribute("aria-expanded", "true");
});

test("shows whole numbers in full", () => {
  // Trailing-zero trimming belongs after a decimal point only: 70 is not 7.
  const spec = { name: "corner_threshold", label: "Corner", type: "number", control: "slider", default: 60, min: 20, max: 150, step: 1, group: "Curves" } as const;
  const { rerender } = render(<ParamControl spec={spec as never} value={70} onChange={() => {}} />);
  expect(screen.getByRole("spinbutton")).toHaveValue(70);
  rerender(<ParamControl spec={spec as never} value={100} onChange={() => {}} />);
  expect(screen.getByRole("spinbutton")).toHaveValue(100);
  const frac = { ...spec, step: 0.05, default: 0.4, min: 0.1, max: 2 };
  rerender(<ParamControl spec={frac as never} value={0.4} onChange={() => {}} />);
  expect(screen.getByRole("spinbutton")).toHaveValue(0.4);
});
