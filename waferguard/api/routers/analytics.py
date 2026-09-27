from __future__ import annotations

import csv
import io
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select

from waferguard.api.db import Alert, AuditLog, Inspection, utcnow
from waferguard.api.routers.inspections import Filters
from waferguard.api.security import Principal, require
from waferguard.api.services import spc
from waferguard.api.services.alerts import alert_dict
from waferguard.api.services.observability import audit

router = APIRouter(prefix="/api/v1", tags=["spc, alerts & audit"])


def _rows(request, f: Filters, limit: int):
    with request.app.state.wg.db.Session() as s:
        rows = s.scalars(f.apply(select(Inspection)).order_by(desc(Inspection.created_at)).limit(limit)).all()
    return list(reversed(rows))  # chronological


def _final(r):
    return r.review_label or r.label


def compute_summary(rows, usl: float) -> dict:
    labels = [_final(r) for r in rows]
    n = len(rows)
    defective = sum(1 for l in labels if l != "none")
    return {
        "total": n, "defective": defective, "defect_rate": defective / n if n else 0.0,
        "by_label": dict(Counter(labels)), "by_severity": dict(Counter(r.severity for r in rows)),
        "needs_review": sum(1 for r in rows if r.needs_review and not r.review_label),
        "mean_confidence": sum(r.confidence for r in rows) / n if n else None,
        "mean_latency_ms": sum(r.latency_ms for r in rows) / n if n else None,
        "capability": spc.capability([r.fail_ratio for r in rows], usl),
        "pareto": spc.pareto(labels),
    }


def _bucket(r, group_by: str) -> str:
    if group_by == "lot":
        return r.lot_id or "(no lot)"
    if group_by == "equipment":
        return r.equipment_id or "(no equipment)"
    fmt = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "shift": None}[group_by]
    if fmt is None:
        h = r.created_at.hour
        return r.created_at.strftime("%Y-%m-%d ") + ("A" if 6 <= h < 14 else "B" if 14 <= h < 22 else "C")
    return r.created_at.strftime(fmt)


@router.get("/spc/summary", summary="KPIs, Cpk/Ppk of fail-die ratio, Pareto")
def summary(request: Request, f: Filters = Depends(), limit: int = Query(5000, le=100000),
            p: Principal = Depends(require("Operator"))):
    st = request.app.state.wg
    return compute_summary(_rows(request, f, limit), st.settings.fail_ratio_usl)


@router.get("/spc/p-chart", summary="p-chart of defective proportion per subgroup")
def p_chart(request: Request, f: Filters = Depends(), group_by: str = Query("hour", pattern="^(lot|hour|day|shift|equipment)$"),
            limit: int = Query(5000, le=100000), p: Principal = Depends(require("Engineer"))):
    groups: dict[str, list[int]] = {}
    for r in _rows(request, f, limit):
        g = groups.setdefault(_bucket(r, group_by), [0, 0])
        g[0] += _final(r) != "none"
        g[1] += 1
    return {"group_by": group_by, **spc.p_chart([(k, d, n) for k, (d, n) in groups.items()])}


@router.get("/spc/imr", summary="Individuals / moving-range chart of fail-die ratio")
def imr(request: Request, f: Filters = Depends(), limit: int = Query(500, le=5000),
        p: Principal = Depends(require("Engineer"))):
    rows = _rows(request, f, limit)
    out = spc.imr_chart([r.fail_ratio for r in rows])
    for pt, r in zip(out.get("points", []), rows):
        pt.update({"id": r.id, "wafer_id": r.wafer_id, "t": r.created_at.isoformat()})
    out["capability"] = spc.capability([r.fail_ratio for r in rows], request.app.state.wg.settings.fail_ratio_usl)
    return out


@router.get("/spc/trend", summary="Defect counts per class over time")
def trend(request: Request, f: Filters = Depends(), group_by: str = Query("day", pattern="^(hour|day|shift|lot|equipment)$"),
          limit: int = Query(20000, le=100000), p: Principal = Depends(require("Operator"))):
    return spc.trend([(_bucket(r, group_by), _final(r)) for r in _rows(request, f, limit)])


@router.get("/spc/equipment", summary="Defect rate per equipment")
def per_equipment(request: Request, p: Principal = Depends(require("Operator"))):
    with request.app.state.wg.db.Session() as s:
        defective = case((func.coalesce(Inspection.review_label, Inspection.label) != "none", 1), else_=0)
        q = select(Inspection.equipment_id, func.count(), func.sum(defective))
        rows = s.execute(q.group_by(Inspection.equipment_id)).all()
    return [{"equipment_id": e or "(none)", "total": int(n), "defective": int(d or 0), "defect_rate": (d or 0) / n}
            for e, n, d in rows]


# ---------------------------------------------------------------- alerts
@router.get("/alerts")
def list_alerts(request: Request, open_only: bool = False, limit: int = Query(100, le=1000),
                p: Principal = Depends(require("Operator"))):
    with request.app.state.wg.db.Session() as s:
        q = select(Alert).order_by(desc(Alert.created_at)).limit(limit)
        if open_only:
            q = q.where(Alert.acknowledged_at.is_(None))
        return [alert_dict(a) for a in s.scalars(q)]


@router.post("/alerts/{aid}/ack")
def ack(aid: int, request: Request, p: Principal = Depends(require("Engineer"))):
    st = request.app.state.wg
    with st.db.Session() as s:
        a = s.get(Alert, aid)
        if not a:
            raise HTTPException(404, "alert not found")
        a.acknowledged_by, a.acknowledged_at = p.username, utcnow()
        s.commit()
        out = alert_dict(a)
    audit(st.db, p, "alert_ack", str(aid))
    st.events.publish("alert.acknowledged", out)
    return out


class AlertRules(BaseModel):
    window: int | None = None
    min_samples: int | None = None
    spike_rate: float | None = None
    spike_factor: float | None = None
    consecutive_defects: int | None = None
    alert_on_critical: bool | None = None
    cooldown_minutes: int | None = None


@router.get("/alerts/rules")
def get_rules(request: Request, p: Principal = Depends(require("Engineer"))):
    st = request.app.state.wg
    n = st.alerts.notifier
    return {"rules": st.settings.alerts.model_dump(),
            "channels": {lvl: n.plan(lvl) for lvl in ("info", "warning", "critical")}}


@router.put("/alerts/rules")
def set_rules(body: AlertRules, request: Request, p: Principal = Depends(require("Manager"))):
    st = request.app.state.wg
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(st.settings.alerts, k, v)
    audit(st.db, p, "alert_rules_updated", None, changes)
    return st.settings.alerts.model_dump()


@router.post("/alerts/test", summary="Send a test alert through every configured channel")
def test_alert(request: Request, p: Principal = Depends(require("Manager"))):
    st = request.app.state.wg
    return st.alerts._raise("test", "warning", None, f"Test alert triggered by {p.username}.", {})


# ---------------------------------------------------------------- audit
@router.get("/audit")
def audit_log(request: Request, username: str | None = None, action: str | None = None,
              limit: int = Query(200, le=5000), offset: int = 0, p: Principal = Depends(require("Manager"))):
    with request.app.state.wg.db.Session() as s:
        q = select(AuditLog).order_by(desc(AuditLog.ts))
        if username:
            q = q.where(AuditLog.username == username)
        if action:
            q = q.where(AuditLog.action == action)
        return [{"id": a.id, "ts": a.ts.isoformat(), "username": a.username, "role": a.role, "action": a.action,
                 "resource": a.resource, "details": a.details, "ip": a.ip} for a in s.scalars(q.limit(limit).offset(offset))]


@router.get("/audit.csv")
def audit_csv(request: Request, p: Principal = Depends(require("Manager"))):
    rows = audit_log(request, limit=5000, p=p)
    out = io.StringIO()
    w = csv.DictWriter(out, ["id", "ts", "username", "role", "action", "resource", "details", "ip"])
    w.writeheader()
    w.writerows(rows)
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})
