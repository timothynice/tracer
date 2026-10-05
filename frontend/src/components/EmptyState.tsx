import { ImagePlus } from "lucide-react";

import { MAX_SIDE } from "@/lib/limits";
import { SAMPLES, sampleUrl } from "@/lib/samples";

export interface EmptyStateProps {
  /** The formats this platform opens, as a phrase ("PNG, JPEG … or BMP"). */
  formats: string;
  /** Whether a file over the cap can be downscaled here (the Mac app). */
  canDownscale?: boolean;
  onOpen: () => void;
  /** A sample was chosen, by its name in `SAMPLES`. */
  onSample: (name: string) => void;
}

/** The viewer with nothing open: a drop target, the open panel, and samples to try. */
export function EmptyState({ formats, canDownscale = true, onOpen, onSample }: EmptyStateProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 p-8 text-center">
      <div className="flex w-full max-w-md flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-muted-foreground/25 px-10 py-12">
        <ImagePlus className="h-9 w-9 text-muted-foreground" aria-hidden="true" />
        <p className="text-[15px] font-semibold">Drop images here</p>
        <p className="text-muted-foreground">{formats}, up to {MAX_SIDE} px a side{canDownscale ? " (larger images can be downscaled)" : ""}</p>
        <button type="button" className="mac-button mt-2" onClick={onOpen}>
          Open…
        </button>
      </div>
      <div className="space-y-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Or try a sample</p>
        <div className="flex flex-wrap justify-center gap-2">
          {SAMPLES.map((s) => (
            <button key={s.name} type="button" className="mac-button gap-2 pl-1.5" onClick={() => onSample(s.name)}>
              <img src={sampleUrl(s.name)} alt="" className="checker h-6 w-6 rounded object-contain" />
              {s.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
