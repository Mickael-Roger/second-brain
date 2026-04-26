// Light/dark theme management.
//
// The active theme is encoded as a class on <html> (`theme-light` or
// no class for dark, the default). CSS variables in index.css take
// over from there. The user's choice is persisted in localStorage and
// re-applied on boot before React mounts to avoid the brief
// dark→light flash on a light-mode visit.

export type Theme = "light" | "dark";

const STORAGE_KEY = "sb.theme";

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "light") root.classList.add("theme-light");
  else root.classList.remove("theme-light");
}

export function currentTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "dark";
}

export function setTheme(theme: Theme): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, theme);
  }
  applyTheme(theme);
}
