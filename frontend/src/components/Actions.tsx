import { Check, Copy, Download, Image as ImageIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { baseName, downloadBlob, svgBlob, svgToPngBlob } from "@/lib/raster";

export interface ActionsProps {
  svg: string | undefined;
  filename: string;
  engine: string;
  width: number;
  height: number;
}

const PNG_SCALES = [1, 2, 4] as const;

export function Actions({ svg, filename, engine, width, height }: ActionsProps) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const disabled = !svg;
  const stem = `${baseName(filename)}-${engine}`;

  const copy = async () => {
    if (!svg) return;
    try {
      await navigator.clipboard.writeText(svg);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard access was blocked");
    }
  };

  const png = async (scale: number) => {
    if (!svg) return;
    setBusy(true);
    try {
      downloadBlob(await svgToPngBlob(svg, width, height, scale), `${stem}@${scale}x.png`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="btn-primary btn-sm" disabled={disabled} onClick={() => svg && downloadBlob(svgBlob(svg), `${stem}.svg`)}>
        <Download className="h-4 w-4" aria-hidden="true" />
        Download SVG
      </button>

      {/* Secondary by weight, not by being harder to use: ghost fill, same height. */}
      <div className="inline-flex h-9 items-center rounded-md bg-muted p-1" role="group" aria-label="Download PNG">
        <span className="flex items-center gap-1.5 pl-1.5 pr-1 text-xs font-medium text-muted-foreground">
          <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
          PNG
        </span>
        {PNG_SCALES.map((s) => (
          <button
            key={s}
            type="button"
            disabled={disabled || busy}
            onClick={() => void png(s)}
            className="tabular inline-flex h-7 items-center justify-center rounded-sm px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:opacity-50"
            aria-label={`Download PNG at ${s}x`}
          >
            {s}×
          </button>
        ))}
      </div>

      <button type="button" className="btn-ghost btn-sm text-muted-foreground hover:text-foreground" disabled={disabled} onClick={() => void copy()} aria-live="polite">
        {copied ? <Check className="h-4 w-4 text-success" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
        {copied ? "Copied" : "Copy SVG"}
      </button>
    </div>
  );
}
