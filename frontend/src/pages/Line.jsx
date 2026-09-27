import { useEffect, useState } from "react";
import { api, authUrl, can, pct } from "../api.js";
import { Sev, name } from "../components/common.jsx";

export default function Line({ events, session, openInspection }) {
  const [feed, setFeed] = useState([]);
  const [cams, setCams] = useState([]);
  const [frames, setFrames] = useState({});
  const [form, setForm] = useState({ camera_id: "line1", source: "0", fps: 5, equipment_id: "", lot_id: "" });
  const [err, setErr] = useState("");

  const refreshCams = () => api("/api/v1/cameras").then(setCams).catch(() => {});
  useEffect(() => {
    api("/api/v1/inspections?limit=24").then((r) => setFeed(r.items));
    refreshCams();
  }, []);

  useEffect(() => events.subscribe((ev) => {
    if (ev.type === "inspection.created") setFeed((f) => [ev.data, ...f].slice(0, 48));
    if (ev.type === "camera.frame") setFrames((m) => ({ ...m, [ev.data.camera]: ev.data }));
    if (ev.type === "camera.status") refreshCams();
  }), [events]);

  const start = async () => {
    setErr("");
    try {
      await api("/api/v1/cameras/start", { method: "POST", body: { ...form, fps: +form.fps, equipment_id: form.equipment_id || null, lot_id: form.lot_id || null } });
      refreshCams();
    } catch (e) { setErr(e.message); }
  };
  const stop = async (id) => { await api(`/api/v1/cameras/${id}/stop`, { method: "POST" }); refreshCams(); };
  const setFps = async (id, fps) => { await api(`/api/v1/cameras/${id}/fps?fps=${fps}`, { method: "POST" }); refreshCams(); };

  const defects = feed.filter((f) => f.label !== "none").length;
  const critical = feed.filter((f) => f.severity === "Critical").length;
  const avgLat = feed.length ? feed.reduce((a, f) => a + (f.latency_ms || 0), 0) / feed.length : null;

  return (
    <>
      <div className="page-head">
        <h1>Line monitor</h1>
        <p>Every wafer inspected anywhere on the line appears here as it happens.</p>
      </div>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="panel kpi"><b>{feed.length}</b><span>recent wafers</span></div>
        <div className="panel kpi"><b>{feed.length ? pct(defects / feed.length, 0) : "–"}</b><span>defective in this window</span></div>
        <div className="panel kpi"><b style={{ color: critical ? "var(--sev-critical)" : undefined }}>{critical}</b><span>critical</span></div>
        <div className="panel kpi"><b>{avgLat ? `${avgLat.toFixed(0)} ms` : "–"}</b><span>mean model latency</span></div>
      </div>

      {cams.filter((c) => c.running).map((c) => (
        <div className="panel" key={c.camera} style={{ marginBottom: 16 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Camera {c.camera}</h2>
            <span className="small muted num">{c.effective_fps} fps effective · {c.frames} frames · {c.errors} errors</span>
          </div>
          <div className="row" style={{ alignItems: "flex-start" }}>
            {frames[c.camera]?.jpeg_b64 && <img alt={`Latest frame from ${c.camera}`} style={{ width: 240, borderRadius: 4, imageRendering: "pixelated" }} src={`data:image/jpeg;base64,${frames[c.camera].jpeg_b64}`} />}
            <div className="stack">
              {frames[c.camera]?.result && <div><Sev s={frames[c.camera].result.severity} /> <b>{name(frames[c.camera].result.label)}</b> <span className="num muted">{pct(frames[c.camera].result.confidence)}</span></div>}
              <label className="row small">Target rate
                <input type="range" min="1" max="30" defaultValue={c.target_fps} onMouseUp={(e) => setFps(c.camera, e.target.value)} onTouchEnd={(e) => setFps(c.camera, e.target.value)} />
                <span className="num">{c.target_fps} fps</span></label>
              <button className="btn secondary" onClick={() => stop(c.camera)}>Stop camera</button>
              {c.last_error && <span className="error small">{c.last_error}</span>}
            </div>
          </div>
        </div>
      ))}

      <div className="feed">
        {feed.map((f) => (
          <div key={f.id} className={`tile ${f.severity}`} onClick={() => openInspection(f.id)} role="button" tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && openInspection(f.id)}>
            <img alt="" loading="lazy" src={authUrl(`/api/v1/inspections/${f.id}/image/annotated`)} onError={(e) => { e.target.style.visibility = "hidden"; }} />
            <div className="small" style={{ marginTop: 6 }}><b>{name(f.label)}</b></div>
            <div className="small muted num">{f.wafer_id || f.id.slice(0, 8)} · {pct(f.confidence, 0)}</div>
          </div>
        ))}
        {!feed.length && <p className="muted">No wafers yet. Inspect one or start a camera below.</p>}
      </div>

      {can(session.role, "Operator") && (
        <div className="panel" style={{ marginTop: 20 }}>
          <h2>Start an inspection camera</h2>
          <p className="small muted">USB/UVC index (0, 1…), an RTSP URL, a GStreamer pipeline (gst:…) for GigE Vision, a GenICam producer (genicam:/path.cti), or synthetic:/folder to replay images.</p>
          <div className="grid cols-3">
            <label className="field">Camera name<input value={form.camera_id} onChange={(e) => setForm({ ...form, camera_id: e.target.value })} /></label>
            <label className="field">Source<input value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} /></label>
            <label className="field">Rate: {form.fps} fps<input type="range" min="1" max="30" value={form.fps} onChange={(e) => setForm({ ...form, fps: e.target.value })} /></label>
            <label className="field">Equipment ID<input value={form.equipment_id} onChange={(e) => setForm({ ...form, equipment_id: e.target.value })} /></label>
            <label className="field">Lot ID<input value={form.lot_id} onChange={(e) => setForm({ ...form, lot_id: e.target.value })} /></label>
            <div className="field" style={{ justifyContent: "flex-end" }}><button className="btn accent" onClick={start}>Start camera</button></div>
          </div>
          {err && <p className="error">{err}</p>}
        </div>
      )}
    </>
  );
}
