// Shell-wide behaviour: theme, keyboard shortcuts, breadcrumbs (SCR-01, DESIGN-SYSTEM d).
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const THEME_KEY = "stratal.theme";

function currentTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** The theme is set before first paint by index.html; this switches and remembers it. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const toggle = useCallback(() => {
    const next: Theme = currentTheme() === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private windows may refuse storage; the theme still switches for this visit.
    }
    setTheme(next);
  }, []);
  return [theme, toggle];
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * Global shortcuts. A key sequence is a space-separated string ("g d"); a sequence's
 * keys must follow each other within a second. Ignored while typing in a field or
 * with a modifier held, so the browser's own shortcuts keep working.
 */
export function useShortcuts(bindings: Record<string, () => void>): void {
  useEffect(() => {
    let pending = "";
    let timer: number | undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (typing(event.target)) return;
      if (document.querySelector("[role=dialog]")) return;
      const key = event.key;
      const sequence = pending ? `${pending} ${key}` : key;
      const action = bindings[sequence];
      if (action) {
        event.preventDefault();
        pending = "";
        action();
        return;
      }
      const prefix = Object.keys(bindings).some((k) => k.startsWith(`${sequence} `));
      pending = prefix ? sequence : "";
      window.clearTimeout(timer);
      if (prefix) timer = window.setTimeout(() => (pending = ""), 1000);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(timer);
    };
  }, [bindings]);
}

/** One part of the location path (SCR-01 region 2). IDs are mono and copy on click. */
export interface Crumb {
  label: string;
  to?: string;
  id?: boolean;
}

export const CrumbContext = createContext<(crumbs: Crumb[]) => void>(() => undefined);

/** A screen states where it is; the shell's top bar shows it. */
export function useCrumbs(crumbs: Crumb[]): void {
  const set = useContext(CrumbContext);
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    set(JSON.parse(key) as Crumb[]);
  }, [key, set]);
}
