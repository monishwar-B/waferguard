from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from waferguard.api.db import Inspection
from waferguard.api.security import Principal, require
from waferguard.api.services.camera import probe_devices
from waferguard.api.services.observability import REGISTRY, audit

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness")
def health():
    return {"status": "ok"}


@router.get("/ready", summary="Readiness: database reachable and model loaded")
def ready(request: Request, response: Response):
    st = request.app.state.wg
    checks = {"database": False, "model": st.models.ready, "queue": st.queue.kind if st.queue else None}
    try:
        checks["database"] = st.db.ping()
    except Exception as exc:  # noqa: BLE001
        checks["database_error"] = str(exc)
    if st.redis is not None:
        try:
            checks["redis"] = bool(st.redis.ping())
        except Exception:  # noqa: BLE001
            checks["redis"] = False
    ok = checks["database"] and checks["model"] and checks.get("redis", True)
    response.status_code = 200 if ok else 503
    return {"ready": ok, **checks}


@router.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- models / A-B
@router.get("/api/v1/models", tags=["models"])
def models_info(request: Request, p: Principal = Depends(require("Operator"))):
    return request.app.state.wg.models.info()


class ABConfig(BaseModel):
    mode: str | None = Field(None, pattern="^(off|shadow|split)$")
    split: float | None = Field(None, ge=0, le=1)
    challenger_dir: str | None = None


@router.put("/api/v1/models/ab", tags=["models"], summary="Configure champion/challenger A/B testing")
def configure_ab(body: ABConfig, request: Request, p: Principal = Depends(require("Admin"))):
    st = request.app.state.wg
    try:
        st.models.configure(body.mode, body.split, body.challenger_dir)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, str(exc))
    audit(st.db, p, "ab_configured", None, body.model_dump(exclude_none=True))
    return st.models.info()


@router.post("/api/v1/models/promote", tags=["models"], summary="Make the challenger the new champion")
def promote(request: Request, p: Principal = Depends(require("Admin"))):
    st = request.app.state.wg
    try:
        st.models.promote()
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    audit(st.db, p, "model_promoted", st.models.champion.version)
    return st.models.info()


@router.post("/api/v1/models/reload", tags=["models"])
def reload(request: Request, p: Principal = Depends(require("Admin"))):
    st = request.app.state.wg
    st.models.reload()
    audit(st.db, p, "model_reloaded")
    return st.models.info()


@router.get("/api/v1/models/ab/stats", tags=["models"], summary="Shadow agreement and reviewed accuracy per variant")
def ab_stats(request: Request, p: Principal = Depends(require("Engineer"))):
    st = request.app.state.wg
    out = {"variants": {}, "shadow": None}
    with st.db.Session() as s:
        for variant, version, n in s.execute(select(Inspection.model_variant, Inspection.model_version, func.count())
                                             .group_by(Inspection.model_variant, Inspection.model_version)).all():
            reviewed = s.execute(select(Inspection.label, Inspection.review_label).where(
                Inspection.model_variant == variant, Inspection.model_version == version,
                Inspection.review_label.is_not(None))).all()
            correct = sum(1 for a, b in reviewed if a == b)
            out["variants"][f"{variant}:{version}"] = {"inspections": n, "reviewed": len(reviewed),
                                                        "reviewed_accuracy": correct / len(reviewed) if reviewed else None}
        shadows = [r for (r,) in s.execute(select(Inspection.shadow).where(Inspection.model_variant == "champion")).all() if r]
    if shadows:
        out["shadow"] = {"compared": len(shadows), "agreement": sum(1 for x in shadows if x.get("agrees")) / len(shadows)}
    return out


# ---------------------------------------------------------------- cameras
class CameraStart(BaseModel):
    camera_id: str = Field("cam1", pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    source: str = Field(..., description="0 | rtsp://... | gst:<pipeline> | genicam:<cti> | synthetic:<folder>")
    fps: float = Field(5, ge=1, le=30)
    equipment_id: str | None = None
    lot_id: str | None = None


@router.get("/api/v1/cameras/devices", tags=["cameras"], summary="Probe local USB/UVC devices")
def devices(p: Principal = Depends(require("Operator"))):
    return probe_devices()


@router.get("/api/v1/cameras", tags=["cameras"])
def cameras(request: Request, p: Principal = Depends(require("Operator"))):
    return request.app.state.wg.cameras.status()


@router.post("/api/v1/cameras/start", tags=["cameras"])
def start_camera(body: CameraStart, request: Request, p: Principal = Depends(require("Operator"))):
    st = request.app.state.wg
    if not st.models.ready:
        raise HTTPException(503, "model not available")
    try:
        status = st.cameras.start(body.camera_id, body.source, body.fps,
                                  {"operator": p.username, "equipment_id": body.equipment_id, "lot_id": body.lot_id})
    except ValueError as exc:
        raise HTTPException(409 if "already running" in str(exc) else 422, str(exc))
    audit(st.db, p, "camera_started", body.camera_id, body.model_dump())
    return status


@router.post("/api/v1/cameras/{cam_id}/stop", tags=["cameras"])
def stop_camera(cam_id: str, request: Request, p: Principal = Depends(require("Operator"))):
    st = request.app.state.wg
    try:
        out = st.cameras.stop(cam_id)
    except KeyError:
        raise HTTPException(404, "camera not found")
    audit(st.db, p, "camera_stopped", cam_id)
    return out


@router.post("/api/v1/cameras/{cam_id}/fps", tags=["cameras"])
def camera_fps(cam_id: str, fps: float, request: Request, p: Principal = Depends(require("Operator"))):
    if not 1 <= fps <= 30:
        raise HTTPException(422, "fps must be between 1 and 30")
    try:
        return request.app.state.wg.cameras.set_fps(cam_id, fps)
    except KeyError:
        raise HTTPException(404, "camera not found")
