import { errorOf, type ImageItem } from "@/state/library";

export interface ImageCardProps {
  item: ImageItem;
  selected: boolean;
  onSelect: () => void;
}

/** One image in the sidebar: its thumbnail, name and size, and a dot while it is queued, tracing or failed. */
export function ImageCard({ item, selected, onSelect }: ImageCardProps) {
  const { image, job } = item;
  const error = errorOf(item);
  const phase = job ? (job.phase === "queued" ? "Queued" : "Tracing…") : error ? "Failed" : null;
  return (
    <div id={`image-${image.id}`} role="option" aria-selected={selected} onClick={onSelect} className="block">
      <div className={`checker relative aspect-[4/3] overflow-hidden rounded-lg ${selected ? "mac-selected" : "ring-1 ring-border"}`}>
        <img src={image.previewUrl} alt="" draggable={false} className="h-full w-full object-contain p-2" />
        {job && <span aria-hidden="true" className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${job.phase === "queued" ? "bg-warning" : "mac-dot animate-pulse"}`} />}
        {!job && error && <span title={error.message} className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-destructive" />}
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
