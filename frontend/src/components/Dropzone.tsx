import { ImagePlus, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";

export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"];
export const MAX_BYTES = 20 * 1024 * 1024;
const ACCEPT_ATTR = ACCEPTED_TYPES.join(",");

export function validateFile(file: File): string | null {
  if (!ACCEPTED_TYPES.includes(file.type)) return `"${file.name}" isn't a supported image. Use PNG, JPG, GIF, WEBP or BMP.`;
  if (file.size > MAX_BYTES) return `"${file.name}" is ${(file.size / 1048576).toFixed(1)} MB; the limit is 20 MB.`;
  return null;
}

export interface DropzoneProps {
  onFile: (file: File) => void;
  disabled?: boolean;
  /** Message shown instead of the call to action while disabled. */
  disabledReason?: string;
  /** Listen for Cmd/Ctrl+V images on the document (default true). */
  paste?: boolean;
  compact?: boolean;
}

export function Dropzone({ onFile, disabled, disabledReason, paste = true, compact }: DropzoneProps) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const accept = useCallback(
    (file: File | null | undefined) => {
      if (!file || disabled) return;
      const problem = validateFile(file);
      if (problem) {
        toast.error(problem);
        return;
      }
      onFile(file);
    },
    [onFile, disabled],
  );

  useEffect(() => {
    if (!paste) return;
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.kind === "file" && i.type.startsWith("image/"));
      if (!item) return;
      e.preventDefault();
      accept(item.getAsFile());
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [paste, accept]);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    accept(e.dataTransfer.files?.[0]);
  };

  const open = () => !disabled && input.current?.click();

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || undefined}
      aria-label="Upload an image"
      onClick={open}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      data-over={over || undefined}
      className={`card flex cursor-pointer flex-col items-center justify-center gap-3 border-2 border-dashed text-center transition-colors
        ${compact ? "p-6" : "px-6 py-16"}
        ${over ? "border-primary bg-accent" : "hover:bg-accent/60"}
        ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      <input ref={input} type="file" accept={ACCEPT_ATTR} className="sr-only" tabIndex={-1} onChange={(e) => (accept(e.target.files?.[0]), (e.target.value = ""))} data-testid="file-input" />
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
        {over ? <ImagePlus className="h-6 w-6" aria-hidden="true" /> : <Upload className="h-6 w-6" aria-hidden="true" />}
      </span>
      {disabled && disabledReason ? (
        <p className="text-sm text-muted-foreground">{disabledReason}</p>
      ) : (
        <>
          <div>
            <p className="text-base font-medium text-foreground">Drop an image, paste, or browse</p>
            <p className="mt-1 text-sm text-muted-foreground">PNG · JPG · GIF · WEBP · BMP — up to 20 MB</p>
          </div>
        </>
      )}
    </div>
  );
}
