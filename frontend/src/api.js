// Thin client for the WaferGuard REST API + WebSocket event stream.
import { useEffect, useRef, useState } from "react";

const TOKEN_KEY = "wg.session";

// Where the backend lives. Empty = same origin (docker / desktop / local dev proxy).
// On Netlify, set VITE_API_BASE to the backend URL, e.g. https://waferguard-api.onrender.com
export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

export function getSession() {
  try {
    return JSON.parse(localStorage.getItem(TOKEN_KEY)) || null;
  } catch {
    return null;
  }
}

export function setSession(s) {
  if (s) localStorage.setItem(TOKEN_KEY, JSON.stringify(s));
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, form, raw } = {}) {
  const s = getSession();
  const headers = {};
  if (s) headers.Authorization = `Bearer ${s.access_token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(API_BASE + path, { method, headers, body: payload });
  if (r.status === 401 && s) {
    setSession(null);
    window.dispatchEvent(new Event("wg-logout"));
  }
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail || detail;
    } catch { /* not json */ }
    throw new ApiError(r.status, detail);
  }
  if (raw) return r;
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

export async function login(username, password) {
  const form = new URLSearchParams({ username, password });
  const r = await fetch(API_BASE + "/api/v1/auth/token", { method: "POST", body: form });
  if (!r.ok) throw new ApiError(r.status, (await r.json()).detail);
  const s = await r.json();
  setSession(s);
  return s;
}

// Authenticated URL for <img> tags and download links (the API accepts ?token=).
export function authUrl(path) {
  const s = getSession();
  const sep = path.includes("?") ? "&" : "?";
  const full = API_BASE + path;
  return s ? `${full}${sep}token=${encodeURIComponent(s.access_token)}` : full;
}

export function qs(obj) {
  const p = new URLSearchParams();
  Object.entries(obj).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

const ROLES = ["Operator", "Engineer", "Manager", "Admin"];
export const can = (role, min) => ROLES.indexOf(role) >= ROLES.indexOf(min);

// Subscribe to server events. Reconnects with back-off; returns connection state.
export function useEvents(onEvent) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let ws;
    let stop = false;
    let delay = 1000;
    const connect = () => {
      const s = getSession();
      if (!s || stop) return;
      const base = API_BASE || location.origin;
      const wsBase = base.replace(/^http/, "ws"); // http->ws, https->wss
      ws = new WebSocket(`${wsBase}/ws?token=${encodeURIComponent(s.access_token)}`);
      ws.onopen = () => { setConnected(true); delay = 1000; };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.type !== "ping") handler.current(ev);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stop) setTimeout(connect, (delay = Math.min(delay * 2, 15000)));
      };
    };
    connect();
    return () => { stop = true; ws && ws.close(); };
  }, []);
  return connected;
}

export const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)}%`);
export const fmtTime = (iso) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString() : "–");
