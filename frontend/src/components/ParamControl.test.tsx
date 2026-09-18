import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { EngineTabs } from "./EngineTabs";
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

test("panel groups by ui.group into drawers, counts what changed, and resets", async () => {
  const onReset = vi.fn();
  const values = { threshold: 10, invert: true, turnpolicy: "black", alphamax: 0.5 };
  render(<ParamPanel engine="potrace" specs={specs} values={values} onChange={() => {}} onReset={onReset} />);
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
  await userEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));
  expect(onReset).toHaveBeenCalled();
});

test("a drawer holding an invalid field opens itself", () => {
  render(<ParamPanel engine="potrace" specs={specs} values={ENGINES[0].defaults} onChange={() => {}} onReset={() => {}} invalidField="threshold" />);
  expect(screen.getByRole("button", { name: /^Bitmap/ })).toHaveAttribute("aria-expanded", "true");
});

test("reset is disabled when values equal defaults", () => {
  render(<ParamPanel engine="potrace" specs={specs} values={ENGINES[0].defaults} onChange={() => {}} onReset={() => {}} />);
  expect(screen.getByRole("button", { name: /reset to defaults/i })).toBeDisabled();
});

test("engine tabs render one tab per engine and switch", () => {
  const onChange = vi.fn();
  render(
    <EngineTabs engines={ENGINES} value="potrace" onChange={onChange}>
      {(e) => <div>panel for {e.id}</div>}
    </EngineTabs>,
  );
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByText("panel for potrace")).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByRole("tab", { name: /vtracer/i }));
  expect(onChange).toHaveBeenCalledWith("vtracer");
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
