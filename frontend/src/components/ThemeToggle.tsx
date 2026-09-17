import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { getTheme, setTheme, watchSystemTheme, type Theme } from "@/lib/theme";

const ORDER: Theme[] = ["system", "light", "dark"];
const ICON = { system: Monitor, light: Sun, dark: Moon };
const LABEL = { system: "System theme", light: "Light theme", dark: "Dark theme" };

export function ThemeToggle() {
  const [theme, set] = useState<Theme>(() => getTheme());
  useEffect(() => watchSystemTheme(getTheme), []);
  const Icon = ICON[theme];
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
  return (
    <button
      type="button"
      className="btn-ghost btn-icon"
      aria-label={`${LABEL[theme]} — switch to ${LABEL[next].toLowerCase()}`}
      title={LABEL[theme]}
      onClick={() => {
        setTheme(next);
        set(next);
      }}
    >
      <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
    </button>
  );
}
