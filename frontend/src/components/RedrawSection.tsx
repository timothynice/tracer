import { Sparkles } from "lucide-react";

import { PHASE_TEXT, redrawErrorText, roughHint } from "@/lib/redraw";
import { redrawOf, type ImageItem } from "@/state/library";

export interface RedrawSectionProps {
  item: ImageItem;
  /** Settings ▸ AI redraw ▸ Suggest for rough images. */
  suggest: boolean;
  onRedraw: () => void;
  onCancel: () => void;
}

/** The chip on a redrawn image, with Show Original and Revert, at the head of the inspector: one row, never wrapped. */
export function RedrawChip({ item, onShowOriginal, onRevert }: { item: ImageItem; onShowOriginal: () => void; onRevert: () => void }) {
  if (!redrawOf(item).active) return null;
  return (
    <div className="flex items-center justify-between gap-1 whitespace-nowrap">
      <span className="mac-tint inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium">
        <span className="mac-dot" aria-hidden="true" />
        AI redraw
      </span>
      <span className="flex shrink-0 items-center">
        <button type="button" className="mac-ghost h-7 px-2 text-[12px]" onClick={onShowOriginal}>
          Show Original
        </button>
        <button type="button" className="mac-ghost h-7 px-2 text-[12px]" onClick={onRevert}>
          Revert
        </button>
      </span>
    </div>
  );
}

/** The quiet hint: the accent dot on a muted tint, never a one-sided border. */
export function RedrawHint({ text }: { text: string }) {
  return (
    <p role="note" className="mac-tint flex items-start gap-2 rounded-lg px-2.5 py-2 text-[12px] leading-snug">
      <span className="mac-dot mt-[5px] shrink-0" aria-hidden="true" />
      {text}
    </p>
  );
}

/** The inspector's AI redraw: the hint, the button (Cancel while it runs), the last failure. */
export function RedrawSection({ item, suggest, onRedraw, onCancel }: RedrawSectionProps) {
  const r = redrawOf(item);
  const hint = suggest && !r.active && !r.phase && !r.pending ? roughHint(item.rough) : null;
  const failure = r.error ? redrawErrorText(r.error) : null;
  return (
    <section aria-label="AI redraw" className="space-y-2">
      {hint && <RedrawHint text={hint} />}
      {r.phase ? (
        <div className="space-y-1.5">
          <p className="flex items-start gap-2 text-[12px] leading-snug text-muted-foreground" aria-live="polite">
            <span className="mac-dot mt-[5px] shrink-0 animate-pulse" aria-hidden="true" />
            <span className="min-w-0">
              <span className="block text-foreground">Redrawing with AI…</span>
              {PHASE_TEXT[r.phase] && <span className="block text-[11px]">{PHASE_TEXT[r.phase]}</span>}
            </span>
          </p>
          <button type="button" className="mac-button h-8 w-full" onClick={onCancel}>
            Cancel Redraw
          </button>
        </div>
      ) : (
        <button type="button" className="mac-button h-8 w-full" onClick={onRedraw}>
          <Sparkles className="h-4 w-4" aria-hidden="true" />
          Redraw with AI…
        </button>
      )}
      {failure && (
        <p role="alert" className="text-[12px] leading-snug text-destructive">
          {failure}
        </p>
      )}
    </section>
  );
}
