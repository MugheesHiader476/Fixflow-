"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const ThemeCtx = createContext<{ dark: boolean; toggle: () => void }>({
  dark: true,
  toggle: () => {},
});

export const useTheme = () => useContext(ThemeCtx);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    let stored: string | null = null;
    try { stored = localStorage.getItem("ff-theme"); } catch { /* Storage may be disabled. */ }
    const applyStoredTheme = window.requestAnimationFrame(() => {
      setDark(stored !== "light");
    });

    return () => window.cancelAnimationFrame(applyStoredTheme);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);

  const toggle = () => {
    setDark(!dark);
    try { localStorage.setItem("ff-theme", dark ? "light" : "dark"); } catch { /* Theme still works in memory. */ }
  };

  return <ThemeCtx.Provider value={{ dark, toggle }}>{children}</ThemeCtx.Provider>;
}
