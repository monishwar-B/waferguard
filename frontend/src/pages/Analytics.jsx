import { useCallback, useEffect, useState } from "react";
import { api, can, fmtTime, pct, qs } from "../api.js";
import { Bars, ControlChart, name } from "../components/common.jsx";

const fmt2 = (v) => (v === null || v === undefined ? "–" : v.toFixed(2));

export function SPC({ session, events }) {
  const [f, setF] = useState({ lot_id: "", equipment_id: "" });
  const [groupBy, setGroupBy] = useState("hour");
  const [sum, setSum] = useState(null);
  const [pc, setPc] = useState(null);
  const [imr, setImr] = useState(null);
  const [eq, setEq] = useState([]);
  const eng = can(session.role, "Engineer");
  const load = useCallback(() => {
    const q = qs(f);
    api(`/api/v1/spc/summary${q}`).then(setSum);
    api("/api/v1/spc/equipment").then(setEq);
    if (eng) {
      api(`/api/v1/spc/p-chart${qs({ ...f, group_by: groupBy })}`).then(setPc);
      api(`/api/v1/spc/imr${q}`).then(setImr);
    }
  }, [f, groupBy, eng]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    let t;
    const off = events.subscribe((ev) => {
      if (ev.type === "inspection.created") { clearTimeout(t); t = setTimeout(load, 1500); }
    });
    return () => { off(); clearTimeout(t); };
  }, [events, load]);

  const cap = sum?.capability || {};
  return (
    <>
      <div className="page-head">
        <h1>Process control</h1>
        <p>Defect proportion control chart, fail-die ratio capability and the defect Pareto. Updates as wafers arrive.</p>
      </div>
      <div className="panel row" style={{ marginBottom: 16 }}>
        <label className="field">Lot<input value={f.lot_id} onChange={(e) => setF({ ...f, lot_id: e.target.value })} /></label>
        <label className="field">Equipment<input value={f.equipment_id} onChange={(e) => setF({ ...f, equipment_id: e.target.value })} /></label>
        <label className="field">Subgroup
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            <option value="hour">Hour</option><option value="shift">Shift</option><option value="day">Day</option><option value="lot">Lot</option><option value="equipment">Equipment</option>
          </select></label>
      </div>
      {sum && (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <div className="panel kpi"><b>{sum.total}</b><span>wafers inspected</span></div>
          <div className="panel kpi"><b>{pct(sum.defect_rate)}</b><span>defective</span></div>
          <div className="panel kpi"><b>{fmt2(cap.cpk)} / {fmt2(cap.ppk)}</b><span>Cpk / Ppk of fail ratio (USL {pct(cap.usl, 0)}) · {cap.rating}</span></div>
          <div className="panel kpi"><b>{sum.needs_review}</b><span>waiting for engineer review</span></div>
        </div>
      )}
      <div className="grid cols-2">
        {eng && (
          <div className="panel">
            <h2>Defective proportion (p-chart)</h2>
            <p className="small muted">Dashed red: 3σ limits for each subgroup size. Amber: process average {pc?.center !== undefined ? pct(pc?.center) : ""}. Red points break a Western Electric rule.</p>
            <ControlChart yMax={1} center={pc?.center} points={(pc?.points || []).map((p) => ({ label: p.key, y: p.p, ucl: p.ucl, lcl: p.lcl, bad: p.out_of_control || !!p.rules }))} />
          </div>
        )}
        {eng && (
          <div className="panel">
            <h2>Fail-die ratio per wafer (individuals chart)</h2>
            <p className="small muted">Limits from the average moving range. σ within {imr?.sigma_within !== undefined ? pct(imr.sigma_within, 2) : "–"}.</p>
            <ControlChart center={imr?.center} points={(imr?.points || []).map((p) => ({ label: p.wafer_id || p.i, y: p.x, ucl: imr.ucl, lcl: imr.lcl, bad: (imr.violations || []).some((v) => v.index === p.i) }))} />
          </div>
        )}
        <div className="panel">
          <h2>Defect Pareto</h2>
          <Bars items={(sum?.pareto || []).map((p) => ({ label: name(p.label), value: p.count, text: `${p.count} · ${pct(p.cumulative, 0)} cum.` }))} />
        </div>
        <div className="panel table-wrap">
          <h2>By equipment</h2>
          <table><thead><tr><th>Equipment</th><th className="num">Wafers</th><th className="num">Defective</th><th className="num">Rate</th></tr></thead>
            <tbody>{eq.sort((a, b) => b.defect_rate - a.defect_rate).map((e) => (
              <tr key={e.equipment_id}><td>{e.equipment_id}</td><td className="num">{e.total}</td><td className="num">{e.defective}</td><td className="num">{pct(e.defect_rate)}</td></tr>
            ))}</tbody></table>
        </div>
      </div>
    </>
  );
}

export function Alerts({ session, events }) {
  const [alerts, setAlerts] = useState([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [rules, setRules] = useState(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api(`/api/v1/alerts${qs({ open_only: openOnly })}`).then(setAlerts), [openOnly]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (can(session.role, "Engineer")) api("/api/v1/alerts/rules").then(setRules); }, [session.role]);
  useEffect(() => events.subscribe((ev) => ev.type.startsWith("alert.") && load()), [events, load]);
  const ack = async (id) => { await api(`/api/v1/alerts/${id}/ack`, { method: "POST" }); load(); };
  const saveRules = async () => {
    const r = rules.rules;
    await api("/api/v1/alerts/rules", { method: "PUT", body: { ...r, window: +r.window, min_samples: +r.min_samples, spike_rate: +r.spike_rate,
      spike_factor: +r.spike_factor, consecutive_defects: +r.consecutive_defects, cooldown_minutes: +r.cooldown_minutes } });
    setMsg("Alert rules saved.");
  };
  const setRule = (k) => (e) => setRules({ ...rules, rules: { ...rules.rules, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value } });

  return (
    <>
      <div className="page-head">
        <h1>Alerts</h1>
        <p>Raised automatically on defect-rate spikes, runs of defective wafers and critical defects.</p>
      </div>
      <div className="panel table-wrap" style={{ marginBottom: 16 }}>
        <label className="row small" style={{ marginBottom: 8 }}><input type="checkbox" style={{ minHeight: "auto" }} checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Only unacknowledged</label>
        <table>
          <thead><tr><th>Time</th><th>Level</th><th>Rule</th><th>Equipment</th><th>Message</th><th>Sent to</th><th></th></tr></thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id}>
                <td className="small">{fmtTime(a.created_at)}</td>
                <td><span className={`sev sev-${a.level === "critical" ? "Critical" : a.level === "warning" ? "Major" : "Minor"}`}>{a.level}</span></td>
                <td>{a.rule.replaceAll("_", " ")}</td><td>{a.equipment_id || "–"}</td><td>{a.message}</td>
                <td className="small">{Object.entries(a.channels || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "dashboard"}</td>
                <td>{a.acknowledged_by ? <span className="small muted">Ack by {a.acknowledged_by}</span> :
                  can(session.role, "Engineer") && <button className="btn secondary small" onClick={() => ack(a.id)}>Acknowledge</button>}</td>
              </tr>
            ))}
            {!alerts.length && <tr><td colSpan="7" className="muted">No alerts. The line is within limits.</td></tr>}
          </tbody>
        </table>
      </div>
      {rules && (
        <div className="panel">
          <h2>Alert rules</h2>
          <div className="grid cols-3">
            <label className="field">Rolling window (wafers)<input inputMode="numeric" value={rules.rules.window} onChange={setRule("window")} /></label>
            <label className="field">Minimum wafers before alerting<input inputMode="numeric" value={rules.rules.min_samples} onChange={setRule("min_samples")} /></label>
            <label className="field">Defect-rate threshold (0–1)<input inputMode="decimal" value={rules.rules.spike_rate} onChange={setRule("spike_rate")} /></label>
            <label className="field">…or times the baseline<input inputMode="decimal" value={rules.rules.spike_factor} onChange={setRule("spike_factor")} /></label>
            <label className="field">Consecutive defective wafers<input inputMode="numeric" value={rules.rules.consecutive_defects} onChange={setRule("consecutive_defects")} /></label>
            <label className="field">Cooldown (minutes)<input inputMode="numeric" value={rules.rules.cooldown_minutes} onChange={setRule("cooldown_minutes")} /></label>
          </div>
          <label className="row small" style={{ marginTop: 10 }}><input type="checkbox" style={{ minHeight: "auto" }} checked={rules.rules.alert_on_critical} onChange={setRule("alert_on_critical")} /> Alert on every Critical wafer</label>
          <p className="small muted">Channels for critical alerts: {rules.channels.critical.join(", ") || "dashboard only (configure email / Teams / SMS in the deployment config)"}</p>
          {can(session.role, "Manager") && (
            <div className="row">
              <button className="btn" onClick={saveRules}>Save rules</button>
              <button className="btn secondary" onClick={() => api("/api/v1/alerts/test", { method: "POST" }).then(() => setMsg("Test alert sent."))}>Send test alert</button>
              {msg && <span className="small">{msg}</span>}
            </div>
          )}
        </div>
      )}
    </>
  );
}
