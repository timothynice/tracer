import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLayerInspector } from "./useLayerInspector";

const open = (body: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">${body}</svg>`;
const A = open('<path d="M0 0h4v4z" fill="#f00"/><path d="M1 1h.5v.5z" fill="#0f0"/>');
const A2 = A.replace("#0f0", "#0f1");
const B = open('<path d="M0 0h2v2z" fill="#00f"/><path d="M2 2h2v2z" fill="#ff0"/>');

describe("useLayerInspector", () => {
  it("keeps the speck threshold through a re-trace of the same image, so an export never regains the specks", () => {
    const { result, rerender } = renderHook(({ svg, id }) => useLayerInspector(svg, id), { initialProps: { svg: A, id: "a" } });
    act(() => result.current.patch({ minArea: 1 }));
    expect(result.current.exportSvg).not.toContain("#0f0");
    rerender({ svg: A2, id: "a" });
    expect(result.current.state.minArea).toBe(1);
    expect(result.current.exportSvg).not.toContain("#0f1");
  });

  it("resets the hidden shapes and the highlight on a new SVG, but not the threshold", () => {
    const { result, rerender } = renderHook(({ svg, id }) => useLayerInspector(svg, id), { initialProps: { svg: A, id: "a" } });
    act(() => result.current.patch({ minArea: 1, hidden: new Set([0]), highlight: 1 }));
    rerender({ svg: A2, id: "a" });
    expect(result.current.state.hidden.size).toBe(0);
    expect(result.current.state.highlight).toBeNull();
    expect(result.current.state.minArea).toBe(1);
  });

  it("holds the threshold per image: another image starts at off and the first keeps its own", () => {
    const { result, rerender } = renderHook(({ svg, id }) => useLayerInspector(svg, id), { initialProps: { svg: A, id: "a" } });
    act(() => result.current.patch({ minArea: 1 }));
    rerender({ svg: B, id: "b" });
    expect(result.current.state.minArea).toBe(0);
    expect(result.current.exportSvg).toBe(B);
    act(() => result.current.patch({ minArea: 3 }));
    rerender({ svg: A, id: "a" });
    expect(result.current.state.minArea).toBe(1);
    rerender({ svg: B, id: "b" });
    expect(result.current.state.minArea).toBe(3);
  });

  it("never applies another SVG's hidden shapes, not even in the first render after the SVG changes", () => {
    const seen: (string | undefined)[] = [];
    const { result, rerender } = renderHook(
      ({ svg }) => {
        const r = useLayerInspector(svg, "a");
        seen.push(r.exportSvg);
        return r;
      },
      { initialProps: { svg: A } },
    );
    act(() => result.current.patch({ hidden: new Set([0]) }));
    seen.length = 0;
    rerender({ svg: B });
    expect(seen.length).toBeGreaterThan(0);
    for (const s of seen) expect(s).toBe(B);
    expect(result.current.liveState.hidden.size).toBe(0);
  });

  it("keeps the panel and display toggles across images", () => {
    const { result, rerender } = renderHook(({ svg, id }) => useLayerInspector(svg, id), { initialProps: { svg: A, id: "a" } });
    act(() => result.current.patch({ open: true, points: true }));
    rerender({ svg: B, id: "b" });
    expect(result.current.state.open).toBe(true);
    expect(result.current.state.points).toBe(true);
  });
});
