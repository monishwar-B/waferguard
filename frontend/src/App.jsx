import { useEffect, useMemo, useRef, useState } from "react";
import { api, can, getSession, login, setSession, useEvents } from "./api.js";
import { InspectionModal } from "./components/common.jsx";
import Admin from "./pages/Admin.jsx";
import { Alerts, SPC } from "./pages/Analytics.jsx";
import Inspect from "./pages/Inspect.jsx";
import Line from "./pages/Line.jsx";
import { Batch, History } from "./pages/Records.jsx";

const PAGES = [
  { id: "inspect", label: "Inspect", min: "Operator" },
  { id: "line", label: "Line monitor", min: "Operator" },
  { id: "history", label: "History", min: "Operator" },
  { id: "batch", label: "Batch", min: "Engineer" },
  { id: "spc", label: "Process control", min: "Operator" },
  { id: "alerts", label: "Alerts", min: "Operator" },
  { id: "admin", label: "Administration", min: "Operator" },
];

function Login({ onLogin }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { onLogin(await login(u, p)); } catch (x) { setErr(x.status === 401 ? "Wrong username or password." : x.message); }
  };
  return (
    <div className="login">
      <form onSubmit={submit}>
        <div className="brand">
          <div className="brand-mark">Wg</div>
          <div><div className="brand-name">WaferGuard</div><div className="brand-tag">Wafer defect inspection</div></div>
        </div>
        <label className="field">Username<input autoFocus autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
        <label className="field">Password<input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
        <button className="btn accent" type="submit">Sign in</button>
        {err && <p className="error">{err}</p>}
      </form>
    </div>
  );
}

// Tiny pub/sub so each page can listen to the single WebSocket.
function useHub() {
  const subs = useRef(new Set());
  const hub = useMemo(() => ({
    subscribe(fn) { subs.current.add(fn); return () => subs.current.delete(fn); },
    emit(ev) { subs.current.forEach((fn) => fn(ev)); },
  }), []);
  return hub;
}

function Shell({ session, onLogout }) {
  const [page, setPage] = useState(() => location.hash.slice(1) || "inspect");
  const [model, setModel] = useState(null);
  const [open, setOpen] = useState(null);
  const [toast, setToast] = useState(null);
  const hub = useHub();
  const connected = useEvents((ev) => {
    hub.emit(ev);
    if (ev.type === "alert.raised") setToast(ev.data);
  });
  const loadModel = () => api("/api/v1/models").then((m) => setModel(m.champion)).catch(() => {});
  useEffect(() => { loadModel(); }, []);
  useEffect(() => { location.hash = page; }, [page]);
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 9000); return () => clearTimeout(t); } }, [toast]);
  const pages = PAGES.filter((p) => can(session.role, p.min));
  const classes = model?.classes || [];
  const props = { session, classes, model, reloadModel: loadModel, events: hub, openInspection: setOpen };
  const acc = model?.test_metrics?.accuracy;
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">Wg</div>
          <div>
            <div className="brand-name">WaferGuard</div>
            <div className="brand-tag">Wafer map defect inspection, WM-811K patterns</div>
          </div>
        </div>
        <div className="topbar-stats">
          <div className="tstat"><div className="tstat-val is-accent">{acc ? `${(acc * 100).toFixed(1)}%` : "–"}</div><div className="tstat-label">Ensemble test accuracy</div></div>
          <div className="tstat-sep" />
          <div className="tstat"><div className="tstat-val">{model?.members?.length ?? "–"}</div><div className="tstat-label">Models combined</div></div>
          <div className="tstat-sep" />
          <div className="tstat"><div className={`tstat-val ${connected ? "is-ok" : "is-bad"}`}>{connected ? "Live" : "Offline"}</div><div className="tstat-label">Line updates</div></div>
          <div className="tstat-sep" />
          <div className="who">
            <div className="who-name">{session.username}<span>{session.role}</span></div>
            <button className="btn secondary small" onClick={onLogout}>Sign out</button>
          </div>
        </div>
      </header>
      <nav className="navtabs" aria-label="Main">
        {pages.map((p) => <button key={p.id} aria-current={page === p.id ? "page" : undefined} onClick={() => setPage(p.id)}>{p.label}</button>)}
      </nav>
      <main>
        {page === "inspect" && <Inspect {...props} />}
        {page === "line" && <Line {...props} />}
        {page === "history" && <History {...props} />}
        {page === "batch" && <Batch {...props} />}
        {page === "spc" && <SPC {...props} />}
        {page === "alerts" && <Alerts {...props} />}
        {page === "admin" && <Admin {...props} />}
      </main>
      {open && <InspectionModal id={open} session={session} classes={classes} onClose={() => setOpen(null)} />}
      {toast && <div className="toast" role="alert" onClick={() => { setPage("alerts"); setToast(null); }}><b>{toast.rule.replaceAll("_", " ")}</b><div className="small muted">{toast.message}</div></div>}
    </div>
  );
}

export default function App() {
  const [session, setS] = useState(getSession);
  useEffect(() => {
    const out = () => setS(null);
    window.addEventListener("wg-logout", out);
    return () => window.removeEventListener("wg-logout", out);
  }, []);
  if (!session) return <Login onLogin={setS} />;
  return <Shell session={session} onLogout={() => { setSession(null); setS(null); }} />;
}
