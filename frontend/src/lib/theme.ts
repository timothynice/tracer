export type Theme = "system" | "light" | "dark";

const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return media().matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", resolveTheme(theme) === "dark");
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
