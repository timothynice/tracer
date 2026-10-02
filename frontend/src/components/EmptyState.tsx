import { ImagePlus } from "lucide-react";

export const SAMPLES = [
  { name: "logo.png", label: "Logo" },
  { name: "sticker.png", label: "Flat art" },
  { name: "gradient.png", label: "Gradient" },
  { name: "shadow.png", label: "Shadow" },
] as const;

export interface EmptyStateProps {
  onOpen: () => void;
  onSample: (file: File) => void;
}

/** The viewer with nothing open: a drop target, the open panel, and samples to try. */
export function EmptyState({ onOpen, onSample }: EmptyStateProps) {
  const pick = async (name: string) => {
    const res = await fetch(`/samples/${name}`);
    const blob = await res.blob();
    onSample(new File([blob], name, { type: blob.type || "image/png" }));
  };
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 p-8 text-center">
      <div className="flex w-full max-w-md flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-muted-foreground/25 px-10 py-12">
        <ImagePlus className="h-9 w-9 text-muted-foreground" aria-hidden="true" />
        <p className="text-[15px] font-semibold">Drop images here</p>
        <p className="text-muted-foreground">PNG, JPEG, GIF, WebP, BMP, HEIC or TIFF, up to 2048 px a side</p>
        <button type="button" className="mac-button mt-2" onClick={onOpen}>
          Open…
        </button>
      </div>
      <div className="space-y-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Or try a sample</p>
        <div className="flex flex-wrap justify-center gap-2">
          {SAMPLES.map((s) => (
            <button key={s.name} type="button" className="mac-button gap-2 pl-1.5" onClick={() => void pick(s.name)}>
              <img src={`/samples/${s.name}`} alt="" className="checker h-6 w-6 rounded object-contain" />
              {s.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
