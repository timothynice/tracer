import * as Tabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

import type { EngineDescription } from "@/lib/api";

export interface EngineTabsProps {
  engines: EngineDescription[];
  value: string;
  onChange: (id: string) => void;
  /** Rendered inside the active tab's panel. */
  children: (engine: EngineDescription) => ReactNode;
}

/** Muted-capsule tabs, one per engine. New backend engines appear automatically. */
export function EngineTabs({ engines, value, onChange, children }: EngineTabsProps) {
  return (
    <Tabs.Root value={value} onValueChange={onChange}>
      <Tabs.List aria-label="Engine" className="grid h-10 rounded-md bg-muted p-1" style={{ gridTemplateColumns: `repeat(${engines.length}, minmax(0, 1fr))` }}>
        {engines.map((e) => (
          <Tabs.Trigger
            key={e.id}
            value={e.id}
            className="inline-flex items-center justify-center gap-1.5 rounded-sm px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
          >
            <span className="dot-brand opacity-0 transition-opacity [[data-state=active]>&]:opacity-100" aria-hidden="true" />
            {e.label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      {engines.map((e) => (
        <Tabs.Content key={e.id} value={e.id} className="pt-4 focus-visible:outline-none">
          <p className="mb-4 text-xs text-muted-foreground">{e.description}</p>
          {children(e)}
        </Tabs.Content>
      ))}
    </Tabs.Root>
  );
}
