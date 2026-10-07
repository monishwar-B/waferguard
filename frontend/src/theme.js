// Light / dark theme. Remembers the choice; the first visit uses the dark theme.
import { useEffect, useState } from "react";

const KEY = "wg.theme";

export function getTheme() {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "light" || t === "dark") return t;
  } catch { /* storage blocked */ }
  return "dark"; // the app's original look; users can switch to light
}

export function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#000000" : "#f2f2ef");
}

export function useTheme() {
  const [theme, setTheme] = useState(getTheme);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const toggle = () => setTheme((cur) => {
    const next = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* ignore */ }
    return next;
  });
  return [theme, toggle];
}
