const SAMPLES = [
  { name: "logo.png", label: "Logo" },
  { name: "sticker.png", label: "Flat art" },
  { name: "gradient.png", label: "Gradient" },
  { name: "shadow.png", label: "Shadow" },
];

export interface SamplesProps {
  onPick: (file: File) => void;
  disabled?: boolean;
}

/** Four bench-corpus images so the tool can be tried without hunting for a file. */
export function Samples({ onPick, disabled }: SamplesProps) {
  const pick = async (name: string) => {
    const res = await fetch(`/samples/${name}`);
    const blob = await res.blob();
    onPick(new File([blob], name, { type: "image/png" }));
  };
  return (
    <div className="flex flex-wrap items-center justify-center gap-3">
      <span className="text-xs text-muted-foreground">Or try a sample</span>
      {SAMPLES.map((s) => (
        <button
          key={s.name}
          type="button"
          disabled={disabled}
          onClick={() => void pick(s.name)}
          className="group flex items-center gap-2 rounded-md border bg-card p-1 pr-3 text-xs font-medium transition-colors hover:bg-accent disabled:opacity-50"
        >
          <img src={`/samples/${s.name}`} alt="" className="checker h-8 w-8 rounded-sm object-contain" />
          {s.label}
        </button>
      ))}
    </div>
  );
}
