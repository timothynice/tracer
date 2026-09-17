import { ImagePlus } from "lucide-react";

import type { HealthState } from "@/hooks/useHealth";
import { StatusPill } from "./StatusPill";
import { ThemeToggle } from "./ThemeToggle";

export interface HeaderProps {
  health: HealthState;
  onNew?: () => void;
}

export function Header({ health, onNew }: HeaderProps) {
  return (
    <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-4 md:px-6">
        <a href="/" className="flex items-center gap-2.5 rounded-md" aria-label="Studi0Trace home">
          <img src="/brand/studi0trace-mark.svg" alt="" className="h-8 w-8" aria-hidden="true" />
          <span className="text-base font-semibold tracking-tight">Studi0Trace</span>
        </a>
        <div className="ml-2 hidden min-[420px]:block">
          <StatusPill status={health.status} health={health.health} attempts={health.attempts} />
        </div>
        <div className="ml-auto flex items-center gap-1">
          {onNew && (
            <button type="button" className="btn-ghost btn-sm" onClick={onNew}>
              <ImagePlus className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">New image</span>
            </button>
          )}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
