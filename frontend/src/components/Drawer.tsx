import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

export interface DrawerProps {
  title: string;
  /** Shown on the right of the header when closed — e.g. how many values differ. */
  badge?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** One collapsible section. Open state is local: it is a reading preference,
 *  not something worth persisting or lifting. */
export function Drawer({ title, badge, defaultOpen = false, children }: DrawerProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section aria-label={title} className="border-b last:border-b-0">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "" : "-rotate-90"}`} aria-hidden="true" />
          <span className="flex-1">{title}</span>
          {!open && badge}
        </button>
      </h3>
      {open && <div className="space-y-4 pb-4">{children}</div>}
    </section>
  );
}
