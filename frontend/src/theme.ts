import { useSyncExternalStore } from "react";

type Theme = "dark" | "light";
const KEY = "lss_theme";
let current: Theme = (localStorage.getItem(KEY) as Theme) || "dark";
const listeners = new Set<() => void>();

export function initTheme() {
  document.documentElement.classList.toggle("dark", current === "dark");
}

export function toggleTheme() {
  current = current === "dark" ? "light" : "dark";
  localStorage.setItem(KEY, current);
  document.documentElement.classList.toggle("dark", current === "dark");
  listeners.forEach((l) => l());
}

export function useTheme(): Theme {
  return useSyncExternalStore((cb) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }, () => current);
}
