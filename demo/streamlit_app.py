"""WaferGuard: Streamlit edition.

Runs the same trained ensemble, localization, severity grading and root-cause
guidance as the full WaferGuard server, in a single Streamlit app that can be
hosted for free on Streamlit Community Cloud (or run locally with
``streamlit run demo/streamlit_app.py``).

Not included (they need a server and database): user accounts, permanent
history, alerts, Redis workers, server-side industrial cameras. Use the full
deployment (docker compose) for those.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys
import tempfile
import zipfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from waferguard.data.io import ImageFormatError, RawSpec, is_supported, load_image_bytes  # noqa: E402
from waferguard.inference.engine import InferenceEngine  # noqa: E402
from waferguard.inference.localization import render  # noqa: E402
from waferguard.knowledge import guidance  # noqa: E402
from waferguard.taxonomy import display_name  # noqa: E402

MODEL_DIR = os.path.join(ROOT, "models", "wafer-ensemble")
SAMPLES = os.path.join(ROOT, "sample_data", "wm811k")
SEV_COLOR = {"Critical": "#e2685c", "Major": "#e39a4f", "Minor": "#ecc066", "None": "#5fbf8b"}

st.set_page_config(page_title="WaferGuard", page_icon="🟠", layout="wide")

# ----------------------------------------------------------------- look & feel
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500&family=IBM+Plex+Mono:wght@500;600&family=Manrope:wght@400;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, p, label, input, button { font-family: 'Manrope', system-ui, sans-serif; }
.stApp {
  background-color: #0a0c10;
  background-image:
    radial-gradient(38rem 30rem at 12% 6%, rgba(217,164,65,.12), transparent 70%),
    radial-gradient(42rem 34rem at 92% 90%, rgba(123,155,194,.11), transparent 70%),
    linear-gradient(rgba(255,255,255,.015) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.015) 1px, transparent 1px);
  background-size: auto, auto, 34px 34px, 34px 34px;
  background-attachment: fixed;
}
h1, h2, h3 { font-family: 'Fraunces', Georgia, serif !important; font-weight: 500 !important; letter-spacing: .1px; }
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
  background: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.012) 42%, transparent), rgba(20,24,32,.55);
  backdrop-filter: blur(18px) saturate(140%); -webkit-backdrop-filter: blur(18px) saturate(140%);
  border: 1px solid rgba(255,255,255,.08) !important; border-radius: 16px !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 22px 44px -24px rgba(0,0,0,.75);
}
[data-testid="stSidebar"] { background: rgba(14,17,23,.85); border-right: 1px solid rgba(255,255,255,.06); }
.mono { font-family: 'IBM Plex Mono', monospace; }
.verdict { font-family: 'Fraunces', serif; font-size: 2.1rem; line-height: 1.1; margin: .2rem 0 .3rem; }
.pill { display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: .75rem; padding: 3px 11px; border-radius: 20px; }
.muted { color: #8890a0; font-size: .86rem; }
.score { display: grid; grid-template-columns: 1fr 64px; gap: 4px 12px; align-items: center; margin: .55rem 0; }
.score .bar { grid-column: 1 / -1; height: 9px; border-radius: 5px; background: rgba(255,255,255,.06); overflow: hidden; }
.score .bar span { display: block; height: 100%; border-radius: 5px; background: linear-gradient(90deg, rgba(123,155,194,.5), #7b9bc2); }
.score.ens .bar span { background: linear-gradient(90deg, rgba(217,164,65,.6), #ecc066); }
.score .v { font-family: 'IBM Plex Mono', monospace; text-align: right; }
.score.ens .v { color: #ecc066; font-weight: 600; }
.cause { padding: .55rem .7rem; border-radius: 8px; background: rgba(10,12,16,.4); border: 1px solid rgba(255,255,255,.05); margin-bottom: .45rem; }
.cause .area { float: right; font-family: 'IBM Plex Mono', monospace; font-size: .7rem; color: #7b9bc2; background: rgba(123,155,194,.14); padding: 1px 8px; border-radius: 20px; }
.recur { padding: .55rem .7rem; border-radius: 8px; background: rgba(123,155,194,.14); border: 1px solid rgba(123,155,194,.3); color: #b9cbe2; font-size: .86rem; margin: .4rem 0; }
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------- model
@st.cache_resource(show_spinner="Loading the inspection model…")
def load_engine() -> InferenceEngine:
    return InferenceEngine(MODEL_DIR, providers="CPUExecutionProvider")


def password_gate() -> None:
    """Optional: set APP_PASSWORD in the Streamlit Cloud secrets to require a password."""
    try:
        required = st.secrets.get("APP_PASSWORD")
    except Exception:  # noqa: BLE001 - no secrets file locally
        required = None
    if not required or st.session_state.get("authed"):
        return
    st.markdown("## WaferGuard")
    pw = st.text_input("Password", type="password")
    if pw and pw == required:
        st.session_state.authed = True
        st.rerun()
    elif pw:
        st.error("Wrong password.")
    st.stop()


def pct(v) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def recurrence(label: str, equipment: str, lot: str) -> dict:
    """Same pattern on the same equipment / lot earlier in this session."""
    hist = st.session_state.history
    out = {"label": label, "equipment": None, "lot": None}
    if label == "none":
        return out
    if equipment:
        rows = [h for h in hist if h["equipment"] == equipment][-25:]
        out["equipment"] = {"id": equipment, "total": len(rows), "same": sum(h["label"] == label for h in rows)}
    if lot:
        rows = [h for h in hist if h["lot"] == lot]
        out["lot"] = {"id": lot, "total": len(rows), "same": sum(h["label"] == label for h in rows)}
    return out


def inspect(engine: InferenceEngine, image: np.ndarray, name: str, lot: str, equipment: str) -> dict:
    r = engine.analyze(image)
    src = image if (r["input_domain"] == "optical" and image.ndim == 3 and image.dtype == np.uint8) else None
    r["annotated"] = render(r["_levels"], r["regions"], r["label"], r["severity"], r["confidence"], src)
    rec_entry = {"time": dt.datetime.now().strftime("%H:%M:%S"), "file": name, "lot": lot, "equipment": equipment,
                 "label": r["label"], "confidence": r["confidence"], "severity": r["severity"],
                 "fail_ratio": r["fail_ratio"], "needs_review": r["needs_review"]}
    st.session_state.history.append(rec_entry)
    r["recurrence"] = recurrence(r["label"], equipment, lot)
    r["guidance"] = guidance(r["label"], r["severity"], r["needs_review"], r["recurrence"])
    r["file"] = name
    return r


def pdf_report(r: dict, lot: str, equipment: str) -> bytes:
    from waferguard.api.services.reports import inspection_pdf
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        r["annotated"].save(f, format="PNG")
        path = f.name
    insp = SimpleNamespace(
        id=f"streamlit-{dt.datetime.now():%Y%m%d%H%M%S}", created_at=dt.datetime.now(), operator="streamlit user",
        equipment_id=equipment or None, lot_id=lot or None, wafer_id=os.path.splitext(r["file"])[0], recipe=None,
        source="upload", model_version=r["model_version"], model_variant="champion", input_domain=r["input_domain"],
        label=r["label"], confidence=r["confidence"], severity=r["severity"], fail_ratio=r["fail_ratio"],
        die_count=r["die_count"], needs_review=r["needs_review"], review_label=None, reviewed_by=None,
        annotated_path=path, probabilities=r["probabilities"], regions=r["regions"])
    try:
        return inspection_pdf(insp, "WaferGuard (Streamlit edition)", r["guidance"])
    finally:
        os.unlink(path)


# ----------------------------------------------------------------- UI pieces
def model_scores(engine: InferenceEngine) -> None:
    info = engine.info()
    st.markdown("### Model accuracy")
    st.markdown(f"<div class='muted'>Measured on {info.get('test_wafers') or 'held-out'} test wafers never used "
                "for training or tuning.</div>", unsafe_allow_html=True)
    names = {"sklearn": "Geometry features + gradient boosting"}
    lo = 0.85
    width = lambda v: max(0.0, (v - lo) / (1 - lo)) * 100  # noqa: E731
    html = ""
    for m in info["members"]:
        w = (m.get("params") or {}).get("width")
        title = names.get(m["type"], f"Residual CNN{f', {w} channels' if w else ''}")
        acc = m.get("test_accuracy")
        if acc is None:
            continue
        html += (f"<div class='score'><div>{title}<div class='muted'>{round(m['weight'] * 100)}% of the vote</div></div>"
                 f"<div class='v'>{pct(acc)}</div><div class='bar'><span style='width:{width(acc):.1f}%'></span></div></div>")
    ens = info["test_metrics"].get("accuracy")
    html += (f"<div class='score ens'><div><b>Combined ensemble</b><div class='muted'>what inspects your wafers</div></div>"
             f"<div class='v'>{pct(ens)}</div><div class='bar'><span style='width:{width(ens or lo):.1f}%'></span></div></div>"
             "<div class='muted mono' style='display:flex;justify-content:space-between'><span>85%</span><span>100%</span></div>")
    st.markdown(html, unsafe_allow_html=True)


def show_result(r: dict, lot: str, equipment: str) -> None:
    c1, c2 = st.columns([1.15, 1], gap="large")
    with c1:
        with st.container(border=True):
            st.image(r["annotated"], width="stretch")
    with c2:
        with st.container(border=True):
            col = SEV_COLOR.get(r["severity"], "#8890a0")
            st.markdown(f"<span class='pill' style='background:{col}22;color:{col}'>● {r['severity']}</span>"
                        f"<div class='verdict'>{display_name(r['label'])}</div>"
                        f"<div class='mono muted'>{pct(r['confidence'])} confidence · {pct(r['fail_ratio'])} failing · "
                        f"{r['latency_ms']:.0f} ms</div>", unsafe_allow_html=True)
            if r["needs_review"]:
                st.warning("Low confidence: an engineer should confirm this result.")
            if r.get("domain_warning"):
                st.error(r["domain_warning"])
            probs = sorted(r["probabilities"].items(), key=lambda kv: -kv[1])[:5]
            for k, v in probs:
                st.progress(float(v), text=f"{display_name(k)}: {pct(v)}")
            st.download_button("Download PDF report", pdf_report(r, lot, equipment),
                               file_name=f"inspection_{os.path.splitext(r['file'])[0]}.pdf",
                               mime="application/pdf", width="stretch")
    g = r["guidance"]
    with st.container(border=True):
        if g["label"] == "none":
            st.markdown("### No defect pattern")
            st.markdown(f"<div class='muted'>{g['summary']} No action needed.</div>", unsafe_allow_html=True)
            return
        a, b = st.columns([1.15, 1], gap="large")
        with a:
            st.markdown("### How this defect forms")
            st.markdown(f"**{g['summary']}**")
            st.markdown(g["mechanism"])
            for n in g["recurrence_notes"]:
                st.markdown(f"<div class='recur'>{n}</div>", unsafe_allow_html=True)
            st.markdown("**Likely causes, most common first**")
            st.markdown("".join(f"<div class='cause'><span class='area'>{c['area']}</span><b>{c['cause']}</b>"
                                f"<div class='muted'>Check: {c['check']}</div></div>" for c in g["causes"]),
                        unsafe_allow_html=True)
        with b:
            st.markdown("### What to do")
            for title, key in (("1. Do now", "immediate"), ("2. Find the cause", "investigate"),
                               ("3. Stop it coming back", "prevent")):
                items = g["actions"].get(key) or []
                if items:
                    st.markdown(f"**{title}**")
                    st.markdown("\n".join(f"- {t}" for t in items))
            st.caption(g["disclaimer"])


def read_upload(data: bytes, name: str, raw: RawSpec | None):
    return load_image_bytes(data, name, raw if name.lower().endswith((".raw", ".bin")) else None)


# ----------------------------------------------------------------- app
password_gate()
engine = load_engine()
st.session_state.setdefault("history", [])

with st.sidebar:
    st.markdown("## WaferGuard")
    st.markdown("<div class='muted'>Wafer-map defect inspection, WM-811K patterns</div>", unsafe_allow_html=True)
    st.divider()
    lot = st.text_input("Lot ID", key="lot")
    equipment = st.text_input("Equipment ID", key="equipment")
    with st.expander("Raw sensor files (.raw / .bin)"):
        rw = st.number_input("Width", 0, 20000, 0)
        rh = st.number_input("Height", 0, 20000, 0)
        rdt = st.selectbox("Pixel type", ["uint8", "uint16", "float32"])
    raw = RawSpec(int(rw), int(rh), rdt) if rw and rh else None
    st.divider()
    model_scores(engine)

st.markdown("# Wafer inspection")
tab_inspect, tab_batch, tab_session, tab_about = st.tabs(["Inspect", "Batch", "This session", "About"])

with tab_inspect:
    src = st.radio("Source", ["Upload a file", "Try a sample wafer", "Phone / webcam photo"], horizontal=True,
                   label_visibility="collapsed")
    image, name = None, None
    if src == "Upload a file":
        f = st.file_uploader("Wafer map: PNG, JPEG, BMP, TIFF, NumPy .npy or raw sensor dump",
                             type=["png", "jpg", "jpeg", "bmp", "tif", "tiff", "npy", "raw", "bin"])
        if f:
            name = f.name
            try:
                image = read_upload(f.getvalue(), f.name, raw)
            except ImageFormatError as e:
                st.error(str(e))
    elif src == "Try a sample wafer":
        samples = sorted(os.listdir(SAMPLES)) if os.path.isdir(SAMPLES) else []
        choice = st.selectbox("Held-out test wafers (the model never saw these)", samples)
        if choice:
            name = choice
            with open(os.path.join(SAMPLES, choice), "rb") as fh:
                image = read_upload(fh.read(), choice, None)
    else:
        shot = st.camera_input("Take a photo of a printed or displayed wafer map")
        if shot:
            name = "camera.jpg"
            image = read_upload(shot.getvalue(), name, None)
    if image is not None:
        key = (name, image.shape, float(image.mean()), lot, equipment)
        if st.session_state.get("last_key") != key:  # only inspect once per input, not on every rerun
            st.session_state.last_key = key
            st.session_state.last_result = inspect(engine, image, name, lot, equipment)
        show_result(st.session_state.last_result, lot, equipment)
    else:
        st.info("Choose a wafer above. Results include the defect pattern, its location, severity, "
                "likely causes and what to do next.")

with tab_batch:
    files = st.file_uploader("Many images or ZIP archives", accept_multiple_files=True,
                             type=["png", "jpg", "jpeg", "bmp", "tif", "tiff", "npy", "zip"], key="batch")
    if files and st.button("Inspect all", type="primary"):
        items = []
        for f in files:
            if f.name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                    items += [(os.path.basename(n), z.read(n)) for n in z.namelist()
                              if not n.endswith("/") and is_supported(n)]
            else:
                items.append((f.name, f.getvalue()))
        rows, bar = [], st.progress(0.0, text="Inspecting…")
        for i, (n, data) in enumerate(items, 1):
            try:
                r = inspect(engine, read_upload(data, n, raw), n, lot, equipment)
                rows.append({"file": n, "pattern": display_name(r["label"]), "confidence": round(r["confidence"], 4),
                             "severity": r["severity"], "fail_ratio": round(r["fail_ratio"], 4),
                             "needs_review": r["needs_review"]})
            except Exception as e:  # noqa: BLE001
                rows.append({"file": n, "pattern": f"error: {e}", "confidence": None, "severity": None,
                             "fail_ratio": None, "needs_review": None})
            bar.progress(i / len(items), text=f"Inspecting… {i}/{len(items)}")
        bar.empty()
        df = pd.DataFrame(rows)
        st.session_state.batch_df = df
    if "batch_df" in st.session_state:
        df = st.session_state.batch_df
        st.dataframe(df, width="stretch", hide_index=True)
        st.download_button("Download results (CSV)", df.to_csv(index=False), "waferguard_batch.csv", "text/csv")

with tab_session:
    hist = pd.DataFrame(st.session_state.history)
    if hist.empty:
        st.info("Wafers you inspect appear here. History is kept until you close or refresh this page.")
    else:
        defective = (hist["label"] != "none").mean()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Wafers inspected", len(hist))
        k2.metric("Defective", pct(defective))
        k3.metric("Critical", int((hist["severity"] == "Critical").sum()))
        k4.metric("Need review", int(hist["needs_review"].sum()))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Defect Pareto")
            par = hist[hist["label"] != "none"]["label"].map(display_name).value_counts()
            if par.empty:
                st.caption("No defects yet.")
            else:
                st.bar_chart(par, color="#d9a441", horizontal=True)
        with c2:
            st.markdown("### By equipment")
            eq = hist.assign(defective=hist["label"] != "none").groupby(hist["equipment"].replace("", "(none)")) \
                .agg(wafers=("label", "size"), defect_rate=("defective", "mean"))
            st.dataframe(eq.style.format({"defect_rate": "{:.1%}"}), width="stretch")
        st.dataframe(hist.assign(label=hist["label"].map(display_name)), width="stretch", hide_index=True)
        st.download_button("Download session history (CSV)", hist.to_csv(index=False), "waferguard_session.csv",
                           "text/csv")

with tab_about:
    info = engine.info()
    st.markdown(f"""
### About this edition
This is the **Streamlit edition** of WaferGuard. It uses the same trained model (version `{info['version']}`),
defect localization, severity grading and root-cause guidance as the full system.

The **full WaferGuard deployment** (Docker) adds user accounts with roles, a permanent database and audit trail,
statistical process control charts, automatic alerts by email, Teams or SMS, batch workers and industrial cameras.

The model recognises the 9 WM-811K wafer-map patterns. Results are decision support: confirm Critical results
with an engineer before acting on the line.
""")
