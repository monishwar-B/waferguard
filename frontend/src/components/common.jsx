import { useEffect, useState } from "react";
import { api, authUrl, can, fmtTime, pct } from "../api.js";
import Guidance from "./Guidance.jsx";

export const DISPLAY = {
  none: "No defect", Center: "Center cluster", Donut: "Donut", "Edge-Loc": "Edge local", "Edge-Ring": "Edge ring",
  Local: "Local cluster", Random: "Random", Scratch: "Scratch", "Near-full": "Near-full failure",
};
export const name = (l) => DISPLAY[l] || l;

export function Sev({ s }) {
  return <span className={`sev sev-${s}`}>{s === "None" ? "Pass" : s}</span>;
}

// Control chart: series of {x label, y}, optional per-point ucl/lcl and centre line.
export function ControlChart({ points, center, height = 220, yMax, yFmt = (v) => pct(v, 0) }) {
  if (!points?.length) return <p className="muted small">No data for this filter yet.</p>;
  const W = 720, H = height, L = 48, R = 12, T = 12, B = 34;
  const ys = points.flatMap((p) => [p.y, p.ucl ?? p.y, p.lcl ?? p.y]).concat(center ?? []);
  const ymax = Math.min(yMax ?? Infinity, Math.max(...ys) * 1.1) || 1, ymin = Math.min(0, ...ys);
  const x = (i) => L + (points.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (points.length - 1));
  const y = (v) => T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin || 1));
  const path = (key) => points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p[key])}`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ymin + f * (ymax - ymin));
  const every = Math.max(1, Math.ceil(points.length / 8));
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="control chart">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line)" />
          <text x={L - 6} y={y(t) + 4} textAnchor="end">{yFmt(t)}</text>
        </g>
      ))}
      {points[0].ucl !== undefined && <path d={path("ucl")} fill="none" stroke="var(--sev-critical)" strokeDasharray="5 4" />}
      {points[0].lcl !== undefined && <path d={path("lcl")} fill="none" stroke="var(--sev-critical)" strokeDasharray="5 4" opacity="0.5" />}
      {center !== undefined && center !== null && <line x1={L} x2={W - R} y1={y(center)} y2={y(center)} stroke="var(--graphite)" strokeWidth="1.5" />}
      <path d={path("y")} fill="none" stroke="var(--ink)" strokeWidth="1.8" />
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(p.y)} r={p.bad ? 5 : 3} fill={p.bad ? "var(--sev-critical)" : "var(--ink)"}>
            <title>{`${p.label}: ${yFmt(p.y)}${p.bad ? " (out of control)" : ""}`}</title>
          </circle>
          {i % every === 0 && <text x={x(i)} y={H - 10} textAnchor="middle">{String(p.label).slice(-11)}</text>}
        </g>
      ))}
    </svg>
  );
}

export function Bars({ items, height = 200 }) {
  if (!items?.length) return <p className="muted small">No defects recorded for this filter.</p>;
  const W = 720, H = height, L = 120, R = 150, rowH = Math.min(26, (H - 10) / items.length);
  const max = Math.max(...items.map((i) => i.value)) || 1;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${items.length * rowH + 10}`} role="img" aria-label="bar chart">
      {items.map((it, i) => (
        <g key={it.label} transform={`translate(0,${i * rowH + 4})`}>
          <text x={L - 8} y={rowH / 2 + 4} textAnchor="end">{it.label}</text>
          <rect x={L} y={3} height={rowH - 8} width={((W - L - R) * it.value) / max} fill={i === 0 ? "var(--ink)" : "var(--graphite)"} rx="5" />
          <text x={L + ((W - L - R) * it.value) / max + 6} y={rowH / 2 + 4}>{it.text ?? it.value}</text>
        </g>
      ))}
    </svg>
  );
}

// Horizontal probability bars; the leading class is highlighted.
export function Probabilities({ probs, top = 4 }) {
  const items = Object.entries(probs || {}).sort((a, b) => b[1] - a[1]).slice(0, top);
  return (
    <div className="stack" style={{ gap: 12 }}>
      {items.map(([k, v], i) => (
        <div className={`prob ${i === 0 ? "top" : ""}`} key={k}>
          <span>{name(k)}</span>
          <span className="prob-bar" role="img" aria-label={`${(v * 100).toFixed(1)} percent`}><i style={{ width: `${Math.max(v * 100, v > 0 ? 1.5 : 0)}%` }} /></span>
          <span className="mono">{pct(v)}</span>
        </div>
      ))}
    </div>
  );
}

export function InspectionModal({ id, session, classes, onClose, onChanged }) {
  const [d, setD] = useState(null);
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    api(`/api/v1/inspections/${id}`).then((r) => { setD(r); setLabel(r.review_label || r.label); }).catch((e) => setErr(e.message));
  }, [id]);
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const save = async () => {
    try {
      const r = await api(`/api/v1/inspections/${id}/review`, { method: "POST", body: { label, note: note || null } });
      setD(r);
      onChanged && onChanged(r);
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Inspection details" onClick={(e) => e.stopPropagation()}>
        {!d ? <p>{err || "Loading inspection…"}</p> : (
          <div className="inspect">
            <div className="stage">
              <div className={`band ${d.severity}`} />
              <div className="view">{d.has_image ? <img alt="Annotated wafer" src={authUrl(`/api/v1/inspections/${id}/image/annotated`)} /> : <p className="empty">Image not stored for this frame.</p>}</div>
            </div>
            <div className="verdict">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <Sev s={d.severity} />
                <button className="btn secondary small" onClick={onClose}>Close</button>
              </div>
              <div className="label">{name(d.review_label || d.label)}</div>
              <div className="conf">Model: {name(d.label)} at {pct(d.confidence)} · fail ratio {pct(d.fail_ratio)}</div>
              <table className="small"><tbody>
                <tr><th>Wafer</th><td>{d.wafer_id || "–"}</td><th>Lot</th><td>{d.lot_id || "–"}</td></tr>
                <tr><th>Equipment</th><td>{d.equipment_id || "–"}</td><th>Operator</th><td>{d.operator}</td></tr>
                <tr><th>Time</th><td colSpan="3">{fmtTime(d.created_at)}</td></tr>
                <tr><th>Model</th><td colSpan="3">{d.model_version} ({d.model_variant})</td></tr>
              </tbody></table>
              <Probabilities probs={d.probabilities} />
              {d.review_label && <p className="small muted">Reviewed by {d.reviewed_by} on {fmtTime(d.reviewed_at)}</p>}
              {can(session.role, "Engineer") && (
                <div className="stack">
                  <label className="field">Confirm or correct the label
                    <select value={label} onChange={(e) => setLabel(e.target.value)}>
                      {classes.map((c) => <option key={c} value={c}>{name(c)}</option>)}
                    </select>
                  </label>
                  <label className="field">Note<textarea value={note} onChange={(e) => setNote(e.target.value)} /></label>
                  <div className="row">
                    <button className="btn" onClick={save}>Save review</button>
                    <a className="btn secondary" href={authUrl(`/api/v1/export/inspections/${id}/report.pdf`)}>Download PDF</a>
                  </div>
                </div>
              )}
              {err && <p className="error">{err}</p>}
            </div>
          </div>
        )}
        {d?.guidance && <div style={{ marginTop: 18 }}><Guidance g={d.guidance} severity={d.severity} /></div>}
      </div>
    </div>
  );
}
