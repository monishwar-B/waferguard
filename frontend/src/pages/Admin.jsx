import { useEffect, useState } from "react";
import { api, authUrl, can, fmtTime, pct } from "../api.js";
import ModelScores from "../components/ModelScores.jsx";

const ROLES = ["Operator", "Engineer", "Manager", "Admin"];

export default function Admin({ session }) {
  const tabs = [
    can(session.role, "Admin") && "Users",
    can(session.role, "Admin") && "API keys",
    "Models",
    can(session.role, "Manager") && "Audit trail",
  ].filter(Boolean);
  const [tab, setTab] = useState(tabs[0]);
  return (
    <>
      <div className="page-head"><h1>Administration</h1></div>
      <div className="tabs" role="tablist">
        {tabs.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}
      </div>
      {tab === "Users" && <Users me={session.username} />}
      {tab === "API keys" && <Keys />}
      {tab === "Models" && <Models session={session} />}
      {tab === "Audit trail" && <Audit />}
    </>
  );
}

function Users({ me }) {
  const [users, setUsers] = useState([]);
  const [f, setF] = useState({ username: "", password: "", role: "Operator", full_name: "" });
  const [err, setErr] = useState("");
  const load = () => api("/api/v1/users").then(setUsers);
  useEffect(() => { load(); }, []);
  const create = async () => {
    setErr("");
    try { await api("/api/v1/users", { method: "POST", body: f }); setF({ username: "", password: "", role: "Operator", full_name: "" }); load(); }
    catch (e) { setErr(e.message); }
  };
  const patch = async (u, body) => { try { await api(`/api/v1/users/${u}`, { method: "PATCH", body }); load(); } catch (e) { setErr(e.message); } };
  return (
    <div className="grid cols-2">
      <div className="panel table-wrap">
        <table><thead><tr><th>User</th><th>Role</th><th>Active</th></tr></thead>
          <tbody>{users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}<div className="small muted">{u.full_name}</div></td>
              <td><select value={u.role} onChange={(e) => patch(u.username, { role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
              <td><input type="checkbox" style={{ minHeight: "auto" }} checked={u.active} disabled={u.username === me} onChange={(e) => patch(u.username, { active: e.target.checked })} /></td>
            </tr>
          ))}</tbody></table>
      </div>
      <div className="panel stack">
        <h2>Add a user</h2>
        <label className="field">Username<input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
        <label className="field">Full name<input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
        <label className="field">Initial password (8+ characters)<input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
        <label className="field">Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></label>
        <button className="btn" onClick={create}>Add user</button>
        {err && <p className="error">{err}</p>}
      </div>
    </div>
  );
}

function Keys() {
  const [keys, setKeys] = useState([]);
  const [f, setF] = useState({ name: "", role: "Operator" });
  const [created, setCreated] = useState(null);
  const load = () => api("/api/v1/api-keys").then(setKeys);
  useEffect(() => { load(); }, []);
  const create = async () => { setCreated(await api("/api/v1/api-keys", { method: "POST", body: f })); load(); };
  return (
    <div className="grid cols-2">
      <div className="panel table-wrap">
        <table><thead><tr><th>Name</th><th>Prefix</th><th>Role</th><th>Created</th><th></th></tr></thead>
          <tbody>{keys.map((k) => (
            <tr key={k.id}><td>{k.name}</td><td className="num">{k.prefix}…</td><td>{k.role}</td><td className="small">{fmtTime(k.created_at)}</td>
              <td>{k.revoked ? <span className="muted small">Revoked</span> : <button className="btn secondary small" onClick={() => api(`/api/v1/api-keys/${k.id}`, { method: "DELETE" }).then(load)}>Revoke</button>}</td></tr>
          ))}</tbody></table>
      </div>
      <div className="panel stack">
        <h2>New key for MES / ERP</h2>
        <p className="small muted">Systems send it in the <code>X-API-Key</code> header. It is shown only once.</p>
        <label className="field">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field">Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></label>
        <button className="btn" onClick={create} disabled={!f.name}>Create key</button>
        {created && <div className="review-flag small">Copy now: <code style={{ wordBreak: "break-all" }}>{created.api_key}</code></div>}
      </div>
    </div>
  );
}

function Models({ session }) {
  const [info, setInfo] = useState(null);
  const [stats, setStats] = useState(null);
  const [ab, setAb] = useState({ mode: "off", split: 0.1, challenger_dir: "" });
  const [err, setErr] = useState("");
  const load = () => {
    api("/api/v1/models").then((i) => { setInfo(i); setAb((a) => ({ ...a, mode: i.ab_mode, split: i.ab_split })); });
    if (can(session.role, "Engineer")) api("/api/v1/models/ab/stats").then(setStats);
  };
  useEffect(() => { load(); }, []);
  const apply = async () => {
    setErr("");
    try {
      await api("/api/v1/models/ab", { method: "PUT", body: { mode: ab.mode, split: +ab.split, ...(ab.challenger_dir ? { challenger_dir: ab.challenger_dir } : {}) } });
      load();
    } catch (e) { setErr(e.message); }
  };
  const card = (title, m) => m && (
    <div className="panel">
      <h2>{title}</h2>
      <table className="small"><tbody>
        <tr><th>Version</th><td className="num">{m.version}</td></tr>
        <tr><th>Held-out test accuracy</th><td className="num">{pct(m.test_metrics?.accuracy, 2)} (macro-F1 {pct(m.test_metrics?.macro_f1, 2)})</td></tr>
        <tr><th>Ensemble</th><td>{m.members.map((x) => `${x.name} ×${x.weight}`).join(", ")}</td></tr>
        <tr><th>Test-time augmentation</th><td>{m.tta ? "8 views" : "off"}</td></tr>
        <tr><th>Runtime</th><td>{m.providers.join(" → ")}</td></tr>
        <tr><th>Classes</th><td>{m.classes.join(", ")}</td></tr>
      </tbody></table>
    </div>
  );
  if (!info) return <p>Loading…</p>;
  return (
    <div className="stack">
      {!info.ready && <div className="warn-flag">No model loaded: {info.error}</div>}
      <ModelScores model={info.champion} />
      <div className="grid cols-2">{card("Serving model details", info.champion)}{card("Challenger", info.challenger)}</div>
      {stats && (
        <div className="panel table-wrap">
          <h2>A/B results</h2>
          <table><thead><tr><th>Variant</th><th className="num">Inspections</th><th className="num">Reviewed</th><th className="num">Reviewed accuracy</th></tr></thead>
            <tbody>{Object.entries(stats.variants).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="num">{v.inspections}</td><td className="num">{v.reviewed}</td><td className="num">{pct(v.reviewed_accuracy)}</td></tr>
            ))}</tbody></table>
          {stats.shadow && <p className="small">Shadow comparisons: {stats.shadow.compared}, agreement with champion {pct(stats.shadow.agreement)}.</p>}
        </div>
      )}
      {can(session.role, "Admin") && (
        <div className="panel">
          <h2>A/B testing</h2>
          <p className="small muted">Shadow scores every wafer with the challenger without showing it to operators. Split serves a share of wafers with the challenger.</p>
          <div className="grid cols-3">
            <label className="field">Mode<select value={ab.mode} onChange={(e) => setAb({ ...ab, mode: e.target.value })}><option value="off">Off</option><option value="shadow">Shadow</option><option value="split">Split traffic</option></select></label>
            <label className="field">Challenger share: {pct(+ab.split, 0)}<input type="range" min="0" max="1" step="0.05" value={ab.split} onChange={(e) => setAb({ ...ab, split: e.target.value })} /></label>
            <label className="field">Challenger model folder (on the server)<input value={ab.challenger_dir} placeholder="models/wafer-ensemble-v2" onChange={(e) => setAb({ ...ab, challenger_dir: e.target.value })} /></label>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" onClick={apply}>Apply</button>
            <button className="btn secondary" disabled={!info.challenger} onClick={() => api("/api/v1/models/promote", { method: "POST" }).then(load).catch((e) => setErr(e.message))}>Promote challenger</button>
            <button className="btn secondary" onClick={() => api("/api/v1/models/reload", { method: "POST" }).then(load)}>Reload from disk</button>
          </div>
          {err && <p className="error">{err}</p>}
        </div>
      )}
    </div>
  );
}

function Audit() {
  const [rows, setRows] = useState([]);
  const [user, setUser] = useState("");
  useEffect(() => { api(`/api/v1/audit${user ? `?username=${encodeURIComponent(user)}` : ""}`).then(setRows); }, [user]);
  return (
    <div className="panel table-wrap">
      <div className="row" style={{ marginBottom: 10 }}>
        <label className="field">User<input value={user} onChange={(e) => setUser(e.target.value)} /></label>
        <a className="btn secondary small" href={authUrl("/api/v1/audit.csv")}>Export CSV</a>
      </div>
      <table><thead><tr><th>Time</th><th>User</th><th>Role</th><th>Action</th><th>Resource</th><th>Details</th><th>IP</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.id}><td className="small">{fmtTime(a.ts)}</td><td>{a.username}</td><td>{a.role}</td><td>{a.action.replaceAll("_", " ")}</td>
            <td className="small num">{a.resource}</td><td className="small">{Object.entries(a.details || {}).map(([k, v]) => `${k}=${v}`).join(", ")}</td><td className="small">{a.ip}</td></tr>
        ))}</tbody></table>
    </div>
  );
}
