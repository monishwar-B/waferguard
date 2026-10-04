import { useEffect, useMemo, useRef, useState } from "react";
import { api, can, getSession, login, setSession, useEvents } from "./api.js";
import { InspectionModal } from "./components/common.jsx";
import { Icon, ThemeToggle } from "./components/icons.jsx";
import { useTheme } from "./theme.js";
import Admin from "./pages/Admin.jsx";
import { Alerts, SPC } from "./pages/Analytics.jsx";
import Inspect from "./pages/Inspect.jsx";
import Line from "./pages/Line.jsx";
import { Batch, History } from "./pages/Records.jsx";

const PAGES = [
  { id: "inspect", icon: "inspect", label: "Inspect", min: "Operator" },
  { id: "line", icon: "line", label: "Line monitor", min: "Operator" },
  { id: "history", icon: "history", label: "History", min: "Operator" },
  { id: "batch", icon: "batch", label: "Batch", min: "Engineer" },
  { id: "spc", icon: "spc", label: "Process control", min: "Operator" },
  { id: "alerts", icon: "alerts", label: "Alerts", min: "Operator" },
  { id: "admin", icon: "admin", label: "Administration", min: "Operator" },
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
      <form onSubmit={submit}>
        <div className="row"><img src="/favicon.svg" width="34" height="34" alt="" /><h1 style={{ margin: 0 }}>WaferGuard</h1></div>
        <p className="tagline">Wafer defect inspection, process control and traceability.</p>
        <label className="field">Username<input autoFocus autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
        <label className="field">Password<input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
        <button className="btn accent" type="submit">Sign in</button>
        {err && <p className="error" role="alert">{err}</p>}
        <p className="login-foot">Authorised personnel only. Activity is recorded in the audit trail.</p>
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

function Shell({ session, onLogout, theme, onToggleTheme }) {
  const [page, setPage] = useState(() => location.hash.slice(1) || "inspect");
  const [classes, setClasses] = useState([]);
  const [open, setOpen] = useState(null);
  const [toast, setToast] = useState(null);
  const hub = useHub();
  const connected = useEvents((ev) => {
    hub.emit(ev);
    if (ev.type === "alert.raised") setToast(ev.data);
  });
  useEffect(() => { api("/api/v1/models").then((m) => setClasses(m.champion?.classes || [])).catch(() => {}); }, []);
  useEffect(() => { location.hash = page; }, [page]);
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 9000); return () => clearTimeout(t); } }, [toast]);
  const pages = PAGES.filter((p) => can(session.role, p.min));
  const props = { session, classes, events: hub, openInspection: setOpen };
  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand"><img src="/favicon.svg" width="26" height="26" alt="" />WaferGuard</div>
        {pages.map((p) => <button key={p.id} aria-current={page === p.id ? "page" : undefined} onClick={() => setPage(p.id)}><Icon name={p.icon} />{p.label}</button>)}
        <div className="spacer" />
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        <div className="who">
          <span className="avatar" aria-hidden="true">{session.username.slice(0, 1)}<span className={`conn ${connected ? "on" : ""}`} title={connected ? "Live updates connected" : "Reconnecting"} /></span>
          <span><b>{session.username}</b>{session.role}</span>
        </div>
        <button onClick={onLogout}><Icon name="logout" />Sign out</button>
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
      {toast && <div className="toast" role="alert" onClick={() => { setPage("alerts"); setToast(null); }}><b>{toast.rule.replaceAll("_", " ")}</b><div className="small">{toast.message}</div></div>}
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
