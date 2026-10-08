import { errorOf, needsUpdate, shownAnswer, type ImageItem } from "@/state/library";

export interface ImageCardProps {
  item: ImageItem;
  selected: boolean;
  onSelect: () => void;
}

/** One image in the sidebar: its thumbnail, name and size; a dot while it is queued, tracing or failed; a badge once traced. */
export function ImageCard({ item, selected, onSelect }: ImageCardProps) {
  const { image, job } = item;
  const error = errorOf(item);
  const phase = job ? (job.phase === "queued" ? "Queued" : "Tracing…") : error ? "Failed" : null;
  const traced = !job && !!shownAnswer(item);
  const stale = traced && needsUpdate(item);
  return (
    <div id={`image-${image.id}`} role="option" aria-selected={selected} onClick={onSelect} className="block">
      <div className={`checker relative aspect-[4/3] overflow-hidden rounded-lg ${selected ? "mac-selected" : "ring-1 ring-border"}`}>
        <img src={image.previewUrl} alt="" draggable={false} className={`h-full w-full object-contain p-2 transition-opacity ${job ? "opacity-60" : ""}`} />
        {job && <span aria-hidden="true" className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${job.phase === "queued" ? "bg-warning" : "mac-dot animate-pulse"}`} />}
        {!job && error && <span title={error.message} className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-destructive" />}
        {traced && (
          <span title={stale ? "Settings changed since this trace" : "Traced"} className={`absolute bottom-1.5 right-1.5 rounded bg-popover/85 px-1 text-[10px] font-semibold leading-4 backdrop-blur ${stale ? "text-warning" : "text-muted-foreground"}`}>
            SVG
          </span>
        )}
      </div>
      <p className="mt-1.5 truncate text-[13px] font-medium" title={image.path ?? image.name}>
        {image.name}
      </p>
      <p className="tabular text-[11px] text-muted-foreground">
        {image.width} × {image.height}
        {phase && ` · ${phase}`}
      </p>
    </div>
  );
}
