import { useCallback, useEffect, useRef, useState } from "react";
import { api, authUrl, can, fmtTime, pct, qs } from "../api.js";
import { Sev, name } from "../components/common.jsx";

export function History({ session, classes, openInspection, events }) {
  const [f, setF] = useState({ lot_id: "", wafer_id: "", equipment_id: "", label: "", severity: "", needs_review: "" });
  const [page, setPage] = useState(0);
  const [data, setData] = useState({ total: 0, items: [] });
  const size = 25;
  const load = useCallback(() => {
    api(`/api/v1/inspections${qs({ ...f, limit: size, offset: page * size })}`).then(setData);
  }, [f, page]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => events.subscribe((ev) => ev.type === "inspection.reviewed" && load()), [events, load]);
  const set = (k) => (e) => { setPage(0); setF({ ...f, [k]: e.target.value }); };
  const filterQs = qs(f);

  return (
    <>
      <div className="page-head">
        <h1>History</h1>
        <p>Every inspection with its full traceability. Select a row to see the wafer, review the label or print its report.</p>
      </div>
      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="grid cols-3">
          <label className="field">Lot<input value={f.lot_id} onChange={set("lot_id")} /></label>
          <label className="field">Wafer contains<input value={f.wafer_id} onChange={set("wafer_id")} /></label>
          <label className="field">Equipment<input value={f.equipment_id} onChange={set("equipment_id")} /></label>
          <label className="field">Pattern<select value={f.label} onChange={set("label")}><option value="">Any</option>{classes.map((c) => <option key={c} value={c}>{name(c)}</option>)}</select></label>
          <label className="field">Severity<select value={f.severity} onChange={set("severity")}><option value="">Any</option>{["Critical", "Major", "Minor", "None"].map((s) => <option key={s}>{s}</option>)}</select></label>
          <label className="field">Review<select value={f.needs_review} onChange={set("needs_review")}><option value="">All</option><option value="true">Waiting for review</option></select></label>
        </div>
        {can(session.role, "Engineer") && (
          <div className="row" style={{ marginTop: 12 }}>
            <a className="btn secondary small" href={authUrl(`/api/v1/export/inspections.csv${filterQs}`)}>Export CSV</a>
            <a className="btn secondary small" href={authUrl(`/api/v1/export/images.zip${filterQs}`)}>Export annotated images</a>
            <a className="btn secondary small" href={authUrl(`/api/v1/export/summary.pdf${filterQs}`)}>Summary PDF</a>
          </div>
        )}
      </div>
      <div className="panel table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Lot</th><th>Wafer</th><th>Equipment</th><th>Pattern</th><th className="num">Confidence</th><th>Severity</th><th>Operator</th><th>Review</th></tr></thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => openInspection(r.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openInspection(r.id)}>
                <td className="small">{fmtTime(r.created_at)}</td><td>{r.lot_id || "–"}</td><td>{r.wafer_id || "–"}</td><td>{r.equipment_id || "–"}</td>
                <td>{name(r.review_label || r.label)}</td><td className="num">{pct(r.confidence)}</td><td><Sev s={r.severity} /></td>
                <td>{r.operator}</td><td className="small">{r.review_label ? `✓ ${r.reviewed_by}` : r.needs_review ? "Waiting" : ""}</td>
              </tr>
            ))}
            {!data.items.length && <tr><td colSpan="9" className="muted">No inspections match these filters.</td></tr>}
          </tbody>
        </table>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 10 }}>
          <span className="small muted num">{data.total} inspections</span>
          <div className="row">
            <button className="btn secondary small" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="small num">Page {page + 1} of {Math.max(1, Math.ceil(data.total / size))}</span>
            <button className="btn secondary small" disabled={(page + 1) * size >= data.total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </div>
      </div>
    </>
  );
}

export function Batch({ events }) {
  const [jobs, setJobs] = useState([]);
  const [lot, setLot] = useState("");
  const [equipment, setEquipment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const ref = useRef();
  const load = () => api("/api/v1/jobs").then(setJobs);
  useEffect(() => { load(); }, []);
  useEffect(() => events.subscribe((ev) => {
    if (ev.type !== "job.progress") return;
    setJobs((js) => js.map((j) => (j.id === ev.data.id ? { ...j, ...ev.data } : j)));
  }), [events]);

  const submit = async () => {
    const files = ref.current.files;
    if (!files.length) { setErr("Choose files or a ZIP archive first."); return; }
    setErr(""); setBusy(true);
    const form = new FormData();
    [...files].forEach((f) => form.append("files", f));
    if (lot) form.append("lot_id", lot);
    if (equipment) form.append("equipment_id", equipment);
    try {
      await api("/api/v1/jobs/batch", { method: "POST", form });
      ref.current.value = "";
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <h1>Batch</h1>
        <p>Queue a whole lot at once: select many images or a ZIP. Workers process them in the background and progress updates live.</p>
      </div>
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="grid cols-3">
          <label className="field">Images or ZIP<input ref={ref} type="file" multiple accept=".zip,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.npy" style={{ paddingTop: 10 }} /></label>
          <label className="field">Lot ID<input value={lot} onChange={(e) => setLot(e.target.value)} /></label>
          <label className="field">Equipment ID<input value={equipment} onChange={(e) => setEquipment(e.target.value)} /></label>
        </div>
        <div className="row" style={{ marginTop: 12 }}><button className="btn accent" disabled={busy} onClick={submit}>{busy ? "Uploading…" : "Queue batch"}</button></div>
        {err && <p className="error">{err}</p>}
      </div>
      <div className="panel table-wrap">
        <table>
          <thead><tr><th>Submitted</th><th>By</th><th>Lot</th><th>Status</th><th>Progress</th><th className="num">Failed</th><th></th></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td className="small">{fmtTime(j.created_at)}</td><td>{j.created_by}</td><td>{j.params?.lot_id || "–"}</td>
                <td>{j.status}</td>
                <td style={{ minWidth: 160 }}><div className="bar top"><span style={{ width: `${j.total ? ((j.done + j.failed) / j.total) * 100 : 0}%` }} /></div><span className="small num">{j.done}/{j.total}</span></td>
                <td className="num">{j.failed}</td>
                <td>{j.status === "done" && <a className="btn secondary small" href={authUrl(`/api/v1/export/inspections.csv?job_id=${j.id}`)}>CSV</a>}</td>
              </tr>
            ))}
            {!jobs.length && <tr><td colSpan="7" className="muted">No batches yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
