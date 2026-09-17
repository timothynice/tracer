/** Client-side SVG → PNG for the download button. */

const ROOT = /<svg\b[^>]*>/i;

/** Give the root element explicit pixel dimensions so <img> sizes it. */
export function withDimensions(svg: string, width: number, height: number): string {
  const m = ROOT.exec(svg);
  if (!m) return svg;
  const root = m[0]
    .replace(/\s+width="[^"]*"/i, "")
    .replace(/\s+height="[^"]*"/i, "")
    .replace(/\/?>$/, (end) => ` width="${width}" height="${height}"${end}`);
  return svg.slice(0, m.index) + root + svg.slice(m.index + m[0].length);
}

export function svgBlob(svg: string): Blob {
  return new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
}

export async function svgToPngBlob(svg: string, width: number, height: number, scale = 1): Promise<Blob> {
  const w = Math.max(1, Math.round(width * scale));
  const h = Math.max(1, Math.round(height * scale));
  const url = URL.createObjectURL(svgBlob(withDimensions(svg, w, h)));
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("The SVG could not be rendered"));
      el.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas is unavailable");
    ctx.drawImage(img, 0, 0, w, h);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encoding failed"))), "image/png"),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function baseName(filename: string): string {
  return filename.replace(/\.[^.]+$/, "") || "trace";
}
