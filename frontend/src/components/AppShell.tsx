import type { ReactNode } from "react";

export interface AppShellProps {
  titleBar: ReactNode;
  sidebar: ReactNode | null;
  main: ReactNode;
  inspector: ReactNode | null;
  /** Painted over everything, e.g. the drop target while files are dragged in. */
  overlay?: ReactNode;
}

/** Three rounded panels on the window: 8 px from its edges and from each other. */
export function AppShell({ titleBar, sidebar, main, inspector, overlay }: AppShellProps) {
  return (
    <div className="app-shell">
      {titleBar}
      <div className="flex min-h-0 flex-1 gap-2 px-2 pb-2">
        {sidebar && (
          <aside aria-label="Images" className="panel-sidebar flex w-[256px] shrink-0 flex-col overflow-hidden">
            {sidebar}
          </aside>
        )}
        <main className="panel relative flex min-w-0 flex-1 flex-col overflow-hidden">{main}</main>
        {inspector && (
          <aside aria-label="Vectorize" className="panel flex w-[300px] shrink-0 flex-col overflow-hidden">
            {inspector}
          </aside>
        )}
      </div>
      {overlay}
    </div>
  );
}
