export type Theme = "system" | "light" | "dark";

const KEY = "studi0trace.theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return media().matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", resolveTheme(theme) === "dark");
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* private mode: theme just won't persist */
  }
  applyTheme(theme);
}

/** Re-apply when the OS theme changes while in "system". Returns an unsubscribe. */
export function watchSystemTheme(getCurrent: () => Theme): () => void {
  const mq = media();
  const onChange = () => {
    if (getCurrent() === "system") applyTheme("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
