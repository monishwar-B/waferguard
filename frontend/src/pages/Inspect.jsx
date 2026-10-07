import { useEffect, useRef, useState } from "react";
import { api, authUrl, fmtTime, pct } from "../api.js";
import { Probabilities, Sev, name } from "../components/common.jsx";
import { Icon } from "../components/icons.jsx";
import Guidance from "../components/Guidance.jsx";
import ModelScores from "../components/ModelScores.jsx";

const ACCEPT = ".png,.jpg,.jpeg,.bmp,.tif,.tiff,.npy,.raw,.bin";

function useSticky(key, init) {
  const [v, setV] = useState(() => localStorage.getItem(key) ?? init);
  useEffect(() => localStorage.setItem(key, v), [key, v]);
  return [v, setV];
}

export default function Inspect({ events, openInspection, model }) {
  const [tab, setTab] = useState("upload");
  const [lot, setLot] = useSticky("wg.lot", "");
  const [equipment, setEquipment] = useSticky("wg.equipment", "");
  const [wafer, setWafer] = useState("");
  const [result, setResult] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [raw, setRaw] = useState({ w: "", h: "", dtype: "uint16" });
  const [over, setOver] = useState(false);
  const [recent, setRecent] = useState([]);
  const [log, setLog] = useState([]);
  const fileRef = useRef();
  const addLog = (msg, cls = "") => setLog((l) => [...l.slice(-30), { msg, cls, k: Math.random() }]);

  // Saved inspections live in the database, so reload them from the server (not from memory) every time
  // this page opens, and whenever a new one is stored (upload, camera or batch).
  const loadRecent = () => api("/api/v1/inspections?limit=8").then((d) => { setRecent(d.items); return d.items; }).catch(() => []);
  const show = (id) => api(`/api/v1/inspections/${id}`).then((r) => {
    setResult(r);
    setPreview(r.has_image ? authUrl(`/api/v1/inspections/${r.id}/image/annotated`) : null);
  }).catch(() => {});
  useEffect(() => {
    // restore the last verdict after reopening the app
    loadRecent().then((items) => { if (items[0]) show(items[0].id); });
  }, []);
  useEffect(() => events?.subscribe((ev) => ev.type === "inspection.created" && loadRecent()), [events]);

  const send = async (file) => {
    setErr(""); setBusy(true);
    const form = new FormData();
    form.append("file", file);
    if (lot) form.append("lot_id", lot);
    if (equipment) form.append("equipment_id", equipment);
    if (wafer) form.append("wafer_id", wafer);
    if (/\.(raw|bin)$/i.test(file.name)) {
      if (!raw.w || !raw.h) { setErr("Raw sensor files need a width and height."); setBusy(false); return; }
      form.append("raw_width", raw.w); form.append("raw_height", raw.h); form.append("raw_dtype", raw.dtype);
    }
    try {
      const r = await api("/api/v1/inspections", { method: "POST", form });
      setResult(r);
      setPreview(authUrl(`/api/v1/inspections/${r.id}/image/annotated`));
      setWafer("");
      loadRecent();
      addLog(`Scan complete · ${r.latency_ms?.toFixed(0)} ms · ${file.name}`);
      addLog(`Pattern ${name(r.label)} · ${pct(r.confidence)} · ${pct(r.fail_ratio)} failing`);
      addLog(r.needs_review ? "VERDICT: NEEDS ENGINEER" : "VERDICT: CLEARED", r.needs_review ? "err" : "ok");
    } catch (e) { setErr(e.message); addLog(e.message, "err"); } finally { setBusy(false); }
  };

  const acc = model?.test_metrics?.accuracy, f1 = model?.test_metrics?.macro_f1;
  return (
    <>
      <div className="head-row">
        <div className="head-copy">
          <span className="label">Inspect · pattern and root cause</span>
          <h1>See the defect. <em>Trace</em> the cause.</h1>
          <p>Upload a wafer map or point a station camera at it. WaferGuard names the pattern, grades the severity and lists what to check first.</p>
        </div>
        <div className="stats" aria-label="Model summary">
          <div className="stat"><b>{typeof acc === "number" ? pct(acc) : "–"}</b><span>Test accuracy</span></div>
          <div className="stat"><b>{model?.members?.length ?? "–"}</b><span>Ensemble models</span></div>
          <div className="stat"><b>{typeof f1 === "number" ? pct(f1) : "–"}</b><span>Macro F1</span></div>
        </div>
      </div>

      <section className="workspace" aria-label="Inspect a wafer">
        <div className="col-left">
          <div className="card input-card">
            <span className="label">Load a wafer</span>
            <div className="tabs" role="tablist" aria-label="Input source">
              <button role="tab" aria-selected={tab === "upload"} onClick={() => setTab("upload")}><Icon name="upload" size={16} />Upload</button>
              <button role="tab" aria-selected={tab === "camera"} onClick={() => setTab("camera")}><Icon name="inspect" size={16} />Camera</button>
            </div>
            <div className="grid cols-2">
              <label className="field">Lot ID<input value={lot} onChange={(e) => setLot(e.target.value)} /></label>
              <label className="field">Equipment ID<input value={equipment} onChange={(e) => setEquipment(e.target.value)} /></label>
            </div>
            {tab === "upload" && (
              <>
                <label className="field">Wafer ID (defaults to file name)<input value={wafer} onChange={(e) => setWafer(e.target.value)} /></label>
                <div className={`dropzone ${over ? "over" : ""}`} onClick={() => fileRef.current.click()}
                  onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
                  onDrop={(e) => { e.preventDefault(); setOver(false); e.dataTransfer.files[0] && send(e.dataTransfer.files[0]); }}>
                  <Icon name="upload" size={22} />
                  <span>Drop a wafer map here<small>or click to browse · PNG JPG BMP TIFF NPY RAW</small></span>
                  <input ref={fileRef} type="file" accept={ACCEPT} hidden onClick={(e) => e.stopPropagation()} onChange={(e) => e.target.files[0] && send(e.target.files[0])} />
                </div>
                <details className="small">
                  <summary>Raw sensor settings</summary>
                  <div className="grid cols-3" style={{ marginTop: 8 }}>
                    <label className="field">Width<input inputMode="numeric" value={raw.w} onChange={(e) => setRaw({ ...raw, w: e.target.value })} /></label>
                    <label className="field">Height<input inputMode="numeric" value={raw.h} onChange={(e) => setRaw({ ...raw, h: e.target.value })} /></label>
                    <label className="field">Pixel type
                      <select value={raw.dtype} onChange={(e) => setRaw({ ...raw, dtype: e.target.value })}>
                        <option>uint8</option><option>uint16</option><option>float32</option>
                      </select></label>
                  </div>
                </details>
                <button className="btn accent block" type="button" disabled={busy} onClick={() => fileRef.current.click()}>
                  {busy ? "Scanning…" : "Scan wafer"}<Icon name="run" size={16} />
                </button>
              </>
            )}
            {err && <p className="error" role="alert">{err}</p>}
          </div>

          <div className="card recent">
            <span className="label">Latest scans</span>
            <div className="table-wrap">
              <table>
                <tbody>
                  {recent.map((r) => (
                    <tr key={r.id} className="clickable" tabIndex={0} onClick={() => { show(r.id); setTab("upload"); }}
                      onDoubleClick={() => openInspection && openInspection(r.id)}
                      onKeyDown={(e) => e.key === "Enter" && show(r.id)}>
                      <td>{r.wafer_id || "–"}</td><td>{name(r.review_label || r.label)}</td>
                      <td><Sev s={r.severity} /></td>
                    </tr>
                  ))}
                  {!recent.length && <tr><td className="muted">Nothing scanned yet.</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="map-hint">Click a row to preview. Double-click to open full details. Everything is also under History.</p>
          </div>
        </div>

        <div className="col-right">
          <div className="card map-card">
            <div className="map-head"><span className="label">Annotated wafer</span><span className="label">{result ? name(result.label) : "No scan yet"}</span></div>
            <div className="stage">
              <div className={`band ${result?.severity || ""}`} />
              <div className="view">
                {tab === "camera" ? <Camera meta={{ lot, equipment }} onResult={setResult} /> :
                  preview ? <img src={preview} alt="Annotated wafer" /> :
                    <p className="empty">Your wafer map appears here, with failing regions outlined.</p>}
              </div>
            </div>
          </div>

          {busy ? (
            <div className="card result" aria-live="polite"><span className="label">Scanning</span><div className="loading-dots"><i /><i /><i /></div></div>
          ) : result ? <Verdict r={result} /> : (
            <div className="card result" aria-live="polite">
              <div><span className="label">Verdict</span><h2 className="state-word">No wafer loaded</h2></div>
              <p className="note">Upload a wafer map or start the camera. The pattern, severity and first checks appear here.</p>
            </div>
          )}
          {log.length > 0 && <div className="log" role="log" aria-label="Scan log">{log.map((l) => <p key={l.k} className={l.cls}>{l.msg}</p>)}</div>}
        </div>
      </section>
      {result?.guidance && <div style={{ marginTop: 24 }}><Guidance g={result.guidance} severity={result.severity} /></div>}
      <div style={{ marginTop: 24 }}><ModelScores model={model} /></div>
    </>
  );
}

function Verdict({ r }) {
  return (
    <div className="result-grid">
      <div className="card result" aria-live="polite">
        <div className="rise"><span className="label">Pattern</span><h2>{name(r.label)}</h2></div>
        <div className="result-row">
          <span className="conf">{pct(r.confidence)}</span>
          <Sev s={r.severity} />
          <span className={`chip ${r.needs_review ? "chip-outline" : "chip-solid"}`}>{r.needs_review ? "Needs engineer" : "Cleared"}</span>
        </div>
        {r.needs_review && <p className="note">Low confidence, so an engineer should confirm this in History.</p>}
        {r.domain_warning && <div className="warn-flag">{r.domain_warning}</div>}
        <div className="divided">
          <div className="sizes">
            <div><b>{pct(r.fail_ratio)}</b><span>Failing dies</span></div>
            <div><b>{r.regions?.length ?? 0}</b><span>Regions</span></div>
            <div><b>{r.latency_ms ? r.latency_ms.toFixed(0) : "–"}</b><span>ms</span></div>
          </div>
        </div>
      </div>
      <div className="card result">
        <span className="label">Confidence by class</span>
        {r.probabilities && <Probabilities probs={r.probabilities} />}
        {r.probable_causes?.length > 0 && <div className="divided"><span className="label">Check first</span><p className="note">{r.probable_causes.join("; ")}</p></div>}
        {r.guidance && r.label !== "none" && <p className="note">Likely causes and next steps are below.</p>}
      </div>
    </div>
  );
}

// Browser-side capture for kiosks with a USB camera attached to the tablet/PC itself.
function Camera({ meta, onResult }) {
  const video = useRef();
  const canvas = useRef();
  const [fps, setFps] = useState(2);
  const [running, setRunning] = useState(false);
  const [store, setStore] = useState(true);
  const [err, setErr] = useState("");
  const [devices, setDevices] = useState([]);
  const [device, setDevice] = useState("");
  const inflight = useRef(false);

  useEffect(() => {
    navigator.mediaDevices?.enumerateDevices().then((d) => setDevices(d.filter((x) => x.kind === "videoinput")));
  }, []);

  useEffect(() => {
    let stream;
    if (!navigator.mediaDevices) { setErr("This browser cannot access cameras (needs HTTPS or localhost)."); return; }
    navigator.mediaDevices.getUserMedia({ video: device ? { deviceId: { exact: device } } : { width: 1280 } })
      .then((s) => { stream = s; video.current.srcObject = s; setErr(""); })
      .catch((e) => setErr(`Camera unavailable: ${e.message}`));
    return () => stream && stream.getTracks().forEach((t) => t.stop());
  }, [device]);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(async () => {
      if (inflight.current || !video.current?.videoWidth) return;
      inflight.current = true;
      const v = video.current, c = canvas.current;
      c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext("2d").drawImage(v, 0, 0);
      try {
        const r = await api("/api/v1/inspections/frame", { method: "POST",
          body: { image_b64: c.toDataURL("image/jpeg", 0.9), lot_id: meta.lot || null, equipment_id: meta.equipment || null, store } });
        onResult(r);
      } catch (e) { setErr(e.message); setRunning(false); } finally { inflight.current = false; }
    }, 1000 / fps);
    return () => clearInterval(id);
  }, [running, fps, store, meta.lot, meta.equipment, onResult]);

  return (
    <div className="stack" style={{ width: "100%", alignItems: "center" }}>
      <video ref={video} autoPlay playsInline muted />
      <canvas ref={canvas} hidden />
      <div className="row">
        {devices.length > 1 && (
          <select value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="">Default camera</option>
            {devices.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label || d.deviceId.slice(0, 8)}</option>)}
          </select>
        )}
        <label className="row small">Rate <input type="range" min="1" max="30" value={fps} onChange={(e) => setFps(+e.target.value)} /> <span className="num">{fps} fps</span></label>
        <label className="row small"><input type="checkbox" checked={store} onChange={(e) => setStore(e.target.checked)} style={{ minHeight: "auto" }} /> Save frames</label>
        <button className={`btn ${running ? "" : "accent"}`} onClick={() => setRunning(!running)}>{running ? "Stop scanning" : "Start scanning"}</button>
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}
