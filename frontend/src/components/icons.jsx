import { useState } from "react";

// Small stroke icons (24x24 grid), drawn inline so there is nothing extra to download.
const PATHS = {
  inspect: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z", "M21 21l-4.35-4.35"],
  line: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  history: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 6v6l4 2"],
  batch: ["M12 2L2 7l10 5 10-5-10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  spc: ["M18 20V10", "M12 20V4", "M6 20v-6"],
  alerts: ["M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9", "M13.73 21a2 2 0 0 1-3.46 0"],
  admin: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"],
  sun: ["M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z", "M12 1v2", "M12 21v2", "M4.22 4.22l1.42 1.42", "M18.36 18.36l1.42 1.42", "M1 12h2", "M21 12h2", "M4.22 19.78l1.42-1.42", "M18.36 5.64l1.42-1.42"],
  moon: ["M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  trash: ["M3 6h18", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M10 11v6", "M14 11v6"],
  eye: ["M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  eyeoff: ["M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94", "M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19", "M14.12 14.12a3 3 0 1 1-4.24-4.24", "M1 1l22 22"],
  upload: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M17 8l-5-5-5 5", "M12 3v12"],
  run: ["M5 12h14", "M13 6l6 6-6 6"],
  check: ["M20 6L9 17l-5-5"],
  warn: ["M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z", "M12 9v4", "M12 17h.01"],
};

export function Icon({ name, size = 18 }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {(PATHS[name] || []).map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}

// Logo: a wafer with its notch, a crosshair and one flagged die.
export function BrandGlyph() {
  return (
    <svg className="brand-glyph" viewBox="0 0 22 22" aria-hidden="true">
      <circle cx="11" cy="11" r="9.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M11 4.5v13M4.5 11h13" stroke="currentColor" strokeWidth="1" opacity=".4" />
      <rect x="13.6" y="5.6" width="4" height="4" rx="1" fill="var(--signal)" />
    </svg>
  );
}

// Login artwork: concentric wafer rings with a flagged edge arc.
export function WaferMark() {
  const edge = Array.from({ length: 22 }, (_, i) => {
    const a = (-60 + i * 7) * Math.PI / 180;
    return <rect key={i} x={120 + 104 * Math.cos(a) - 3} y={120 + 104 * Math.sin(a) - 3} width="6" height="6" rx="1.5" fill="var(--signal)" />;
  });
  return (
    <svg className="login-mark" viewBox="0 0 240 240" aria-hidden="true" fill="none" stroke="currentColor">
      <circle cx="120" cy="120" r="112" strokeWidth="1.5" opacity=".5" />
      <circle cx="120" cy="120" r="80" strokeWidth="1" opacity=".25" strokeDasharray="2 6" />
      <circle cx="120" cy="120" r="48" strokeWidth="1" opacity=".25" strokeDasharray="2 6" />
      <path d="M120 8v224M8 120h224" strokeWidth="1" opacity=".18" />
      <path d="M108 232a12 12 0 0 1 24 0" strokeWidth="1.5" opacity=".5" />
      <g stroke="none">{edge}</g>
      <circle cx="120" cy="120" r="3" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ThemeToggle({ theme, onToggle, className = "" }) {
  const dark = theme === "dark";
  return (
    <button type="button" className={`theme-toggle ${className}`} onClick={onToggle}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title={dark ? "Light theme" : "Dark theme"}>
      <Icon name={dark ? "sun" : "moon"} size={16} />
      <span>{dark ? "Light mode" : "Dark mode"}</span>
    </button>
  );
}

// Password box with a show / hide button. Drop-in replacement for <input type="password" />.
export function PasswordInput({ className = "", ...props }) {
  const [shown, setShown] = useState(false);
  return (
    <div className={`pw-wrap ${className}`}>
      <input {...props} type={shown ? "text" : "password"} />
      <button type="button" className="pw-toggle" onClick={() => setShown((v) => !v)}
              aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown}
              title={shown ? "Hide password" : "Show password"}>
        <Icon name={shown ? "eyeoff" : "eye"} size={18} />
      </button>
    </div>
  );
}
