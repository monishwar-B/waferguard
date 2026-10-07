import { useEffect, useMemo, useRef, useState } from "react";
import { api, can, getSession, login, setSession, useEvents } from "./api.js";
import { InspectionModal } from "./components/common.jsx";
import { BrandGlyph, Icon, PasswordInput, ThemeToggle, WaferMark } from "./components/icons.jsx";
import { useTheme } from "./theme.js";
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

function Login({ onLogin, theme, onToggleTheme }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { onLogin(await login(u, p)); } catch (x) { setErr(x.status === 401 ? "Wrong username or password." : x.message); }
  };
  return (
    <div className="login">
      <ThemeToggle className="floating" theme={theme} onToggle={onToggleTheme} />
      <div className="wrap login-grid">
        <div className="login-hero">
          <span className="label">WaferGuard · fab quality console</span>
          <h1>Spot it early. <em>Fix</em> it at the source.</h1>
          <p>Sign in to inspect wafer maps, watch the line live and follow the process-control trend.</p>
          <WaferMark />
        </div>
        <form className="card" onSubmit={submit}>
          <div className="brand" style={{ padding: 0 }}><BrandGlyph />WaferGuard</div>
          <label className="field">Username<input autoFocus autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
          <label className="field">Password<PasswordInput autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
          <button className="btn accent" type="submit">Enter console<Icon name="run" size={16} /></button>
          {err && <p className="error" role="alert">{err}</p>}
        </form>
      </div>
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

function Shell({ session, onLogout, theme, onToggleTheme }) {
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
  return (
    <div className="app">
      <aside className="rail">
        <a className="brand" href="#inspect" aria-label="WaferGuard home" onClick={() => setPage("inspect")}><BrandGlyph />WaferGuard</a>
        <span className="label rail-label">Workspace</span>
        <nav className="nav-links" aria-label="Main">
          {pages.map((p) => (
            <button key={p.id} aria-current={page === p.id ? "page" : undefined} onClick={() => setPage(p.id)}><Icon name={p.id} size={18} />{p.label}</button>
          ))}
        </nav>
        <div className="rail-foot">
          <span className="status" role="status" data-state={connected ? "ready" : "off"}><span className="status-dot" aria-hidden="true" /><span className="status-text">{connected ? "Live feed" : "Offline"}</span></span>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <div className="who">
            <span className="avatar" aria-hidden="true">{session.username.slice(0, 1)}</span>
            <div className="who-name">{session.username}<span>{session.role}</span></div>
            <button className="btn secondary small" onClick={onLogout}>Sign out</button>
          </div>
        </div>
      </aside>
      <div className="content">
        <main className="wrap">
          {page === "inspect" && <Inspect {...props} />}
          {page === "line" && <Line {...props} />}
          {page === "history" && <History {...props} />}
          {page === "batch" && <Batch {...props} />}
          {page === "spc" && <SPC {...props} />}
          {page === "alerts" && <Alerts {...props} />}
          {page === "admin" && <Admin {...props} />}
        </main>
        <footer className="footer"><div className="wrap"><span>WaferGuard · wafer inspection console</span><span>FastAPI · ONNX Runtime · WM-811K</span></div></footer>
      </div>
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
  const [theme, toggleTheme] = useTheme();
  if (!session) return <Login onLogin={setS} theme={theme} onToggleTheme={toggleTheme} />;
  return <Shell session={session} theme={theme} onToggleTheme={toggleTheme} onLogout={() => { setSession(null); setS(null); }} />;
}
