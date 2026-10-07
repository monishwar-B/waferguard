
import base64
import datetime as dt
import io
import os
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from waferguard.api.db import Inspection, Job, utcnow
from waferguard.api.security import Principal, oauth2, require, resolve_principal, role_at_least
from waferguard.api.services import filestore, reports
from waferguard.api.services.inspections import inspection_dict, with_guidance
from waferguard.api.services.observability import audit
from waferguard.data.io import ImageFormatError, RawSpec, is_supported, load_image_bytes
from waferguard.data.preprocess import to_levels
from waferguard.inference.engine import ModelLoadError

router = APIRouter(prefix="/api/v1", tags=["inspections"])


def user_or_query_token(request: Request, token: str | None = Depends(oauth2)) -> Principal:
    """Like current_user, but also accepts ?token= so <img src> and download links work."""
    return resolve_principal(request, token or request.query_params.get("token"))


def _state(request):
    return request.app.state.wg


def _raw_spec(width, height, dtype, channels):
    if width and height:
        return RawSpec(int(width), int(height), dtype or "uint8", int(channels or 1))
    return None


def _run(st, img, meta, raw):
    try:
        return st.inspections.inspect(img, meta, raw_bytes=raw)
    except ModelLoadError as exc:
        raise HTTPException(503, f"model not available: {exc}")


def filtered(q, *, lot_id=None, wafer_id=None, equipment_id=None, label=None, severity=None, operator=None,
             source=None, since=None, until=None, needs_review=None, job_id=None):
    if lot_id:
        q = q.where(Inspection.lot_id == lot_id)
    if wafer_id:
        q = q.where(Inspection.wafer_id.like(f"%{wafer_id}%"))
    if equipment_id:
        q = q.where(Inspection.equipment_id == equipment_id)
    if label:
        q = q.where(func.coalesce(Inspection.review_label, Inspection.label) == label)
    if severity:
        q = q.where(Inspection.severity == severity)
    if operator:
        q = q.where(Inspection.operator == operator)
    if source:
        q = q.where(Inspection.source == source)
    if since:
        q = q.where(Inspection.created_at >= since)
    if until:
        q = q.where(Inspection.created_at <= until)
    if needs_review is True:
        q = q.where(Inspection.needs_review.is_(True), Inspection.review_label.is_(None))
    elif needs_review is False:
        q = q.where(Inspection.needs_review.is_(False))
    if job_id:
        q = q.where(Inspection.job_id == job_id)
    return q


class Filters:
    def __init__(self, lot_id: str | None = None, wafer_id: str | None = None, equipment_id: str | None = None,
                 label: str | None = None, severity: str | None = None, operator: str | None = None,
                 source: str | None = None, since: dt.datetime | None = None, until: dt.datetime | None = None,
                 needs_review: bool | None = None, job_id: str | None = None):
        self.kw = {k: v for k, v in locals().items() if k != "self"}

    def apply(self, q):
        return filtered(q, **self.kw)


@router.post("/inspections", summary="Inspect one wafer image (PNG/JPEG/BMP/TIFF/NPY/raw)")
async def inspect_upload(request: Request, file: UploadFile = File(...), lot_id: str | None = Form(None),
                         wafer_id: str | None = Form(None), equipment_id: str | None = Form(None),
                         recipe: str | None = Form(None), raw_width: int | None = Form(None),
                         raw_height: int | None = Form(None), raw_dtype: str | None = Form(None),
                         raw_channels: int | None = Form(None), p: Principal = Depends(require("Operator"))):
    st = _state(request)
    data = await file.read()
    if len(data) > st.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file larger than {st.settings.max_upload_mb} MB")
    try:
        img = load_image_bytes(data, file.filename, _raw_spec(raw_width, raw_height, raw_dtype, raw_channels))
    except ImageFormatError as exc:
        raise HTTPException(415, str(exc))
    meta = {"operator": p.username, "lot_id": lot_id, "wafer_id": wafer_id or os.path.splitext(file.filename or "")[0],
            "equipment_id": equipment_id, "recipe": recipe, "filename": file.filename,
            "source": "api" if p.via == "api_key" else "upload"}
    out = _run(st, img, meta, data)
    audit(st.db, p, "inspect", out["id"], {"label": out["label"], "lot_id": lot_id, "wafer_id": meta["wafer_id"]})
    return out


class FrameIn(BaseModel):
    image_b64: str
    lot_id: str | None = None
    wafer_id: str | None = None
    equipment_id: str | None = None
    store: bool = True


@router.post("/inspections/frame", summary="Inspect a frame captured by a browser/kiosk camera (base64)")
def inspect_frame(body: FrameIn, request: Request, p: Principal = Depends(require("Operator"))):
    st = _state(request)
    try:
        data = base64.b64decode(body.image_b64.split(",")[-1])
        img = load_image_bytes(data, "frame.jpg")
    except (ImageFormatError, ValueError) as exc:
        raise HTTPException(415, f"bad frame: {exc}")
    meta = {"operator": p.username, "lot_id": body.lot_id, "equipment_id": body.equipment_id, "source": "camera",
            "wafer_id": body.wafer_id, "filename": "frame.jpg", "store": body.store}
    return _run(st, img, meta, data if body.store else None)


@router.get("/inspections")
def list_inspections(request: Request, f: Filters = Depends(), limit: int = Query(50, le=500), offset: int = 0,
                     p: Principal = Depends(require("Operator"))):
    st = _state(request)
    with st.db.Session() as s:
        base = f.apply(select(Inspection))
        total = s.scalar(select(func.count()).select_from(base.subquery()))
        rows = s.scalars(base.order_by(desc(Inspection.created_at)).limit(limit).offset(offset)).all()
        return {"total": total, "items": [inspection_dict(r, full=False) for r in rows]}


def _get(st, iid) -> Inspection:
    with st.db.Session() as s:
        r = s.get(Inspection, iid)
    if not r:
        raise HTTPException(404, "inspection not found")
    return r


@router.get("/inspections/{iid}")
def get_inspection(iid: str, request: Request, p: Principal = Depends(require("Operator"))):
    st = _state(request)
    return with_guidance(st.db, _get(st, iid))


@router.get("/inspections/{iid}/image/{kind}", summary="kind = source | annotated | mask")
def get_image(iid: str, kind: str, request: Request, p: Principal = Depends(user_or_query_token)):
    r = _get(_state(request), iid)
    path = {"source": r.image_path, "annotated": r.annotated_path, "mask": r.mask_path}.get(kind)
    if kind not in ("source", "annotated", "mask"):
        raise HTTPException(404, "unknown image kind")
    data = filestore.get(path)
    if data is None:
        raise HTTPException(404, "image not stored")
    return Response(data, media_type=filestore.media_type(path), headers={"Cache-Control": "private, max-age=3600"})


def _downsample_levels(levels, size: int):
    """Shrink a 0/1/2 die map to at most size x size cells (a cell is 'fail' when >= 35 % of its wafer dies fail)."""
    import numpy as np

    h, w = levels.shape
    rows, cols = min(size, h), min(size, w)
    out = np.zeros((rows, cols), np.uint8)
    ri = np.array_split(np.arange(h), rows)
    ci = np.array_split(np.arange(w), cols)
    for i, rr in enumerate(ri):
        for j, cc in enumerate(ci):
            block = levels[np.ix_(rr, cc)]
            fail, ok = int((block == 2).sum()), int((block == 1).sum())
            if fail + ok == 0:
                continue
            out[i, j] = 2 if fail / (fail + ok) >= 0.35 else 1
    return out


@router.get("/inspections/{iid}/diemap", summary="Die matrix of the stored wafer: 0 off-wafer, 1 pass, 2 fail")
def get_diemap(iid: str, request: Request, size: int = Query(48, ge=16, le=96), p: Principal = Depends(user_or_query_token)):
    r = _get(_state(request), iid)
    data = filestore.get(r.image_path)
    if data is None:
        raise HTTPException(404, "image not stored")
    try:
        levels, _ = to_levels(load_image_bytes(data, r.image_path))
    except Exception:  # noqa: BLE001 - raw sensor files need their geometry, which is not stored
        raise HTTPException(422, "die map not available for this input")
    grid = _downsample_levels(levels, size)
    return {"rows": int(grid.shape[0]), "cols": int(grid.shape[1]),
            "grid": ["".join(str(int(v)) for v in row) for row in grid],
            "pass": int((grid == 1).sum()), "fail": int((grid == 2).sum())}


class ReviewIn(BaseModel):
    label: str
    note: str | None = None


@router.post("/inspections/{iid}/review", summary="Engineer confirms or corrects the classification")
def review(iid: str, body: ReviewIn, request: Request, p: Principal = Depends(require("Engineer"))):
    st = _state(request)
    classes = st.models.champion.classes if st.models.champion else None
    if classes and body.label not in classes:
        raise HTTPException(422, f"label must be one of {classes}")
    with st.db.Session() as s:
        r = s.get(Inspection, iid)
        if not r:
            raise HTTPException(404, "inspection not found")
        before = r.review_label or r.label
        r.review_label, r.review_note, r.reviewed_by, r.reviewed_at = body.label, body.note, p.username, utcnow()
        r.needs_review = False
        s.commit()
    out = with_guidance(st.db, _get(st, iid))
    audit(st.db, p, "review", iid, {"from": before, "to": body.label, "note": body.note})
    st.events.publish("inspection.reviewed", {"id": iid, "label": body.label, "by": p.username})
    return out


# ---------------------------------------------------------------- batch jobs
@router.post("/jobs/batch", status_code=202, summary="Queue many images (files and/or ZIP archives)")
async def batch(request: Request, files: list[UploadFile] = File(...), lot_id: str | None = Form(None),
                equipment_id: str | None = Form(None), recipe: str | None = Form(None),
                raw_width: int | None = Form(None), raw_height: int | None = Form(None),
                raw_dtype: str | None = Form(None), p: Principal = Depends(require("Engineer"))):
    st = _state(request)
    job = Job(created_by=p.username, params={"lot_id": lot_id, "equipment_id": equipment_id})
    with st.db.Session() as s:
        s.add(job)
        s.commit()
        job_id = job.id
    folder = os.path.join(st.settings.storage_dir, "batch", job_id)
    os.makedirs(folder, exist_ok=True)
    items = []

    def add(name, data):
        name = os.path.basename(name)
        if not name or not is_supported(name):
            return
        path = os.path.join(folder, f"{len(items):06d}_{name}")
        with open(path, "wb") as fh:
            fh.write(data)
        items.append({"path": path, "filename": name})

    for f in files:
        data = await f.read()
        if (f.filename or "").lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        if not n.endswith("/"):
                            add(n, z.read(n))
            except zipfile.BadZipFile:
                raise HTTPException(415, f"{f.filename} is not a valid ZIP")
        else:
            add(f.filename or "upload", data)
    if not items:
        raise HTTPException(422, "no supported images in the upload")
    raw = {"width": raw_width, "height": raw_height, "dtype": raw_dtype or "uint8"} if raw_width and raw_height else None
    meta = {"operator": p.username, "lot_id": lot_id, "equipment_id": equipment_id, "recipe": recipe, "raw_spec": raw}
    with st.db.Session() as s:
        j = s.get(Job, job_id)
        j.total = len(items)
        s.commit()
    st.queue.submit(job_id, items, meta)
    audit(st.db, p, "batch_submitted", job_id, {"files": len(items), "lot_id": lot_id})
    return {"id": job_id, "status": "queued", "total": len(items), "queue": st.queue.kind}


def _job(j: Job) -> dict:
    return {"id": j.id, "created_at": j.created_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "created_by": j.created_by, "status": j.status, "total": j.total, "done": j.done, "failed": j.failed,
            "params": j.params, "error": j.error}


@router.get("/jobs")
def list_jobs(request: Request, limit: int = 50, p: Principal = Depends(require("Engineer"))):
    with _state(request).db.Session() as s:
        return [_job(j) for j in s.scalars(select(Job).order_by(desc(Job.created_at)).limit(limit))]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, p: Principal = Depends(require("Operator"))):
    with _state(request).db.Session() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return _job(j)


# ---------------------------------------------------------------- exports
def _rows(st, f: Filters, limit=100_000):
    with st.db.Session() as s:
        return s.scalars(f.apply(select(Inspection)).order_by(desc(Inspection.created_at)).limit(limit)).all()


@router.get("/export/inspections.csv")
def export_csv(request: Request, f: Filters = Depends(), p: Principal = Depends(user_or_query_token)):
    if not role_at_least(p.role, "Engineer"):
        raise HTTPException(403, "requires role Engineer or higher")
    st = _state(request)
    audit(st.db, p, "export_csv", None, {k: str(v) for k, v in f.kw.items() if v is not None})
    return Response(reports.inspections_csv(_rows(st, f)), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inspections.csv"})


@router.get("/export/images.zip")
def export_zip(request: Request, f: Filters = Depends(), limit: int = Query(1000, le=20000),
               p: Principal = Depends(user_or_query_token)):
    if not role_at_least(p.role, "Engineer"):
        raise HTTPException(403, "requires role Engineer or higher")
    st = _state(request)
    audit(st.db, p, "export_images", None, {"limit": limit})
    return Response(reports.images_zip(_rows(st, f, limit)), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=inspections_annotated.zip"})


@router.get("/export/inspections/{iid}/report.pdf")
def export_inspection_pdf(iid: str, request: Request, p: Principal = Depends(user_or_query_token)):
    st = _state(request)
    r = _get(st, iid)
    audit(st.db, p, "export_pdf", iid)
    return Response(reports.inspection_pdf(r, p.username, with_guidance(st.db, r)["guidance"]), media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=inspection_{iid}.pdf"})


@router.get("/export/summary.pdf", summary="Lot / equipment / period summary report")
def export_summary_pdf(request: Request, f: Filters = Depends(), p: Principal = Depends(user_or_query_token)):
    if not role_at_least(p.role, "Engineer"):
        raise HTTPException(403, "requires role Engineer or higher")
    from waferguard.api.routers.analytics import compute_summary
    st = _state(request)
    rows = _rows(st, f, 20000)
    stats = compute_summary(rows, st.settings.fail_ratio_usl)
    title = "Inspection summary" + (f" - lot {f.kw['lot_id']}" if f.kw.get("lot_id") else "")
    audit(st.db, p, "export_summary_pdf", f.kw.get("lot_id"))
    return Response(reports.summary_pdf(title, {k: v for k, v in f.kw.items() if v}, stats, rows, p.username),
                    media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=summary.pdf"})
