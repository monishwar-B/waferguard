import { useEffect, useRef, useState } from "react";
import { api, authUrl, fmtTime, pct } from "../api.js";
import { Probabilities, Sev, name } from "../components/common.jsx";

const ACCEPT = ".png,.jpg,.jpeg,.bmp,.tif,.tiff,.npy,.raw,.bin";

function useSticky(key, init) {
  const [v, setV] = useState(() => localStorage.getItem(key) ?? init);
  useEffect(() => localStorage.setItem(key, v), [key, v]);
  return [v, setV];
}

export default function Inspect({ events, openInspection }) {
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
  const fileRef = useRef();

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
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <h1>Inspect</h1>
        <p>Load a wafer map or scan from a camera. The result is saved with the lot, wafer and equipment IDs below.</p>
      </div>
      <div className="inspect">
        <div className="stack">
          <div className="stage">
            <div className={`band ${result?.severity || ""}`} />
            <div className="view">
              {tab === "camera" ? <Camera meta={{ lot, equipment }} onResult={setResult} /> :
                preview ? <img src={preview} alt="Annotated wafer" /> :
                  <p className="empty">Drop a wafer image on the panel at right, or pick one with “Choose file”.</p>}
            </div>
          </div>
        </div>
        <div className="stack">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "upload"} onClick={() => setTab("upload")}>File</button>
            <button role="tab" aria-selected={tab === "camera"} onClick={() => setTab("camera")}>Station camera</button>
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
                <p style={{ margin: "0 0 10px" }}>PNG, JPEG, BMP, TIFF, NumPy or raw sensor files</p>
                <button className="btn accent" disabled={busy} type="button">{busy ? "Inspecting…" : "Choose file"}</button>
                <input ref={fileRef} type="file" accept={ACCEPT} hidden onChange={(e) => e.target.files[0] && send(e.target.files[0])} />
              </div>
              <details className="small">
                <summary>Raw sensor geometry</summary>
                <div className="grid cols-3" style={{ marginTop: 8 }}>
                  <label className="field">Width<input inputMode="numeric" value={raw.w} onChange={(e) => setRaw({ ...raw, w: e.target.value })} /></label>
                  <label className="field">Height<input inputMode="numeric" value={raw.h} onChange={(e) => setRaw({ ...raw, h: e.target.value })} /></label>
                  <label className="field">Pixel type
                    <select value={raw.dtype} onChange={(e) => setRaw({ ...raw, dtype: e.target.value })}>
                      <option>uint8</option><option>uint16</option><option>float32</option>
                    </select></label>
                </div>
              </details>
            </>
          )}
          {err && <p className="error">{err}</p>}
          {result && <Verdict r={result} />}
          <div className="panel">
            <b>Recent inspections</b>
            <div className="table-wrap">
              <table>
                <tbody>
                  {recent.map((r) => (
                    <tr key={r.id} className="clickable" tabIndex={0} onClick={() => { show(r.id); setTab("upload"); }}
                      onDoubleClick={() => openInspection && openInspection(r.id)}
                      onKeyDown={(e) => e.key === "Enter" && show(r.id)}>
                      <td>{r.wafer_id || "–"}</td><td>{name(r.review_label || r.label)}</td>
                      <td><Sev s={r.severity} /></td><td className="small muted">{fmtTime(r.created_at)}</td>
                    </tr>
                  ))}
                  {!recent.length && <tr><td className="muted">Nothing inspected yet.</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="small muted" style={{ marginBottom: 0 }}>Click to view a saved result. Everything is also listed under History.</p>
          </div>
        </div>
      </div>
    </>
  );
}

function Verdict({ r }) {
  return (
    <div className="panel verdict">
      <Sev s={r.severity} />
      <div className="label">{name(r.label)}</div>
      <div className="conf num">{pct(r.confidence)} confidence · {pct(r.fail_ratio)} failing · {r.latency_ms?.toFixed(0)} ms</div>
      {r.needs_review && <div className="review-flag small">Low confidence: an engineer should confirm this result in History.</div>}
      {r.domain_warning && <div className="warn-flag small">{r.domain_warning}</div>}
      {r.probabilities && <Probabilities probs={r.probabilities} />}
      {r.regions?.length > 0 && <p className="small muted">{r.regions.length} defect region{r.regions.length > 1 ? "s" : ""} outlined on the image.</p>}
      {r.probable_causes?.length > 0 && (
        <div className="small"><b>Check first:</b> {r.probable_causes.join("; ")}</div>
      )}
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
      <div className="row" style={{ color: "#dfe3e8" }}>
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
