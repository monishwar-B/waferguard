"""The inspection pipeline shared by uploads, batch jobs, the camera loop and the MES API."""
from __future__ import annotations

import datetime as dt
import logging
import os

import numpy as np
from PIL import Image

from waferguard.api.db import Inspection
from waferguard.api.services.observability import ALERTS, INSPECTIONS, LATENCY
from sqlalchemy import desc, func, select

from waferguard.inference.localization import render
from waferguard.knowledge import guidance

log = logging.getLogger("waferguard.inspect")


class InspectionMeta(dict):
    """operator, equipment_id, lot_id, wafer_id, recipe, source, filename, job_id"""


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


class InspectionService:
    def __init__(self, state):
        self.state = state

    def _paths(self, insp_id: str) -> tuple[str, str]:
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y/%m/%d")
        d = os.path.join(self.state.settings.storage_dir, day)
        os.makedirs(d, exist_ok=True)
        return d, insp_id

    def _store(self, insp_id: str, image: np.ndarray, result: dict, raw_bytes: bytes | None, filename: str | None):
        d, base = self._paths(insp_id)
        ext = os.path.splitext(filename or "")[1].lower() or ".png"
        img_path = os.path.join(d, f"{base}_src{ext}")
        if raw_bytes is not None:
            with open(img_path, "wb") as fh:
                fh.write(raw_bytes)
        else:
            img_path = os.path.join(d, f"{base}_src.png")
            arr = image if image.dtype == np.uint8 else (255 * (image - image.min()) / max(float(np.ptp(image)), 1e-6)).astype(np.uint8)
            Image.fromarray(arr).save(img_path)
        # colour-coded / level maps are re-rendered in the calm die-map palette; camera photos are shown as captured
        src = image if (result["input_domain"] == "optical" and image.dtype == np.uint8
                        and image.shape[:2] == result["_levels"].shape) else None
        ann = render(result["_levels"], result["regions"], result["label"], result["severity"], result["confidence"], src)
        ann_path = os.path.join(d, f"{base}_annotated.png")
        ann.save(ann_path)
        mask_path = None
        if result.get("_mask") is not None:
            mask_path = os.path.join(d, f"{base}_mask.png")
            Image.fromarray((result["_mask"] > 0).astype(np.uint8) * 255).save(mask_path)
        return img_path, ann_path, mask_path

    def inspect(self, image: np.ndarray, meta: dict, raw_bytes: bytes | None = None, publish: bool = True) -> dict:
        engine, variant, shadow_engine = self.state.models.route()
        result = engine.analyze(image)
        shadow = None
        if shadow_engine is not None:
            try:
                sr = shadow_engine.analyze(image, localize=False)
                shadow = {"label": sr["label"], "confidence": sr["confidence"], "severity": sr["severity"],
                          "model_version": sr["model_version"], "agrees": sr["label"] == result["label"]}
            except Exception as exc:  # noqa: BLE001 - a broken challenger must never break production
                log.error("shadow model failed: %s", exc)
        insp = Inspection(
            operator=meta.get("operator") or "unknown", equipment_id=meta.get("equipment_id"),
            lot_id=meta.get("lot_id"), wafer_id=meta.get("wafer_id"), recipe=meta.get("recipe"),
            source=meta.get("source", "upload"), filename=meta.get("filename"), job_id=meta.get("job_id"),
            label=result["label"], confidence=result["confidence"], severity=result["severity"],
            needs_review=result["needs_review"], fail_ratio=result["fail_ratio"], die_count=result["die_count"],
            probabilities=result["probabilities"], regions=result["regions"], input_domain=result["input_domain"],
            model_version=result["model_version"], model_variant=variant, shadow=shadow, latency_ms=result["latency_ms"],
        )
        with self.state.db.Session() as s:
            s.add(insp)
            s.flush()
            if self.state.settings.store_images and meta.get("store", True):
                insp.image_path, insp.annotated_path, insp.mask_path = self._store(
                    insp.id, image, result, raw_bytes, meta.get("filename"))
            s.commit()
        INSPECTIONS.labels(result["label"], result["severity"], insp.source, variant).inc()
        LATENCY.labels(variant).observe(result["latency_ms"] / 1000.0)
        out = {**public(result), "id": insp.id, "created_at": insp.created_at.isoformat(), "model_variant": variant,
               "operator": insp.operator, "equipment_id": insp.equipment_id, "lot_id": insp.lot_id,
               "wafer_id": insp.wafer_id, "source": insp.source}
        rec = recurrence(self.state.db, insp)
        out["recurrence"] = rec
        out["guidance"] = guidance(insp.label, insp.severity, insp.needs_review, rec)
        alerts = self.state.alerts.evaluate(insp)
        for a in alerts:
            ALERTS.labels(a["rule"], a["level"]).inc()
        out["alerts"] = [a["id"] for a in alerts]
        if publish:
            self.state.events.publish("inspection.created", summary(out))
        return out


def summary(d: dict) -> dict:
    keys = ("id", "created_at", "label", "display_name", "confidence", "severity", "needs_review", "fail_ratio",
            "equipment_id", "lot_id", "wafer_id", "source", "operator", "model_version", "model_variant", "latency_ms")
    return {k: d.get(k) for k in keys}


def inspection_dict(i: Inspection, full: bool = True) -> dict:
    d = {
        "id": i.id, "created_at": i.created_at.isoformat(), "operator": i.operator, "equipment_id": i.equipment_id,
        "lot_id": i.lot_id, "wafer_id": i.wafer_id, "recipe": i.recipe, "source": i.source, "filename": i.filename,
        "label": i.label, "confidence": i.confidence, "severity": i.severity, "needs_review": i.needs_review,
        "fail_ratio": i.fail_ratio, "die_count": i.die_count, "model_version": i.model_version,
        "model_variant": i.model_variant, "latency_ms": i.latency_ms, "job_id": i.job_id,
        "review_label": i.review_label, "reviewed_by": i.reviewed_by,
        "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None, "input_domain": i.input_domain,
        "has_image": bool(i.annotated_path),
    }
    if full:
        d.update({"probabilities": i.probabilities, "regions": i.regions, "shadow": i.shadow, "review_note": i.review_note})
    return d


def recurrence(db, insp: Inspection, window: int = 25) -> dict:
    """How often this wafer's pattern appeared on the same equipment / lot, up to this wafer's time."""
    label = insp.review_label or insp.label
    final = func.coalesce(Inspection.review_label, Inspection.label)
    out = {"label": label, "equipment": None, "lot": None}
    if label == "none":
        return out
    with db.Session() as s:
        if insp.equipment_id:
            rows = s.execute(select(final).where(Inspection.equipment_id == insp.equipment_id,
                                                 Inspection.created_at <= insp.created_at)
                             .order_by(desc(Inspection.created_at)).limit(window)).scalars().all()
            out["equipment"] = {"id": insp.equipment_id, "total": len(rows), "same": sum(1 for r in rows if r == label)}
        if insp.lot_id:
            rows = s.execute(select(final).where(Inspection.lot_id == insp.lot_id,
                                                 Inspection.created_at <= insp.created_at)).scalars().all()
            out["lot"] = {"id": insp.lot_id, "total": len(rows), "same": sum(1 for r in rows if r == label)}
    return out


def with_guidance(db, insp: Inspection) -> dict:
    d = inspection_dict(insp)
    rec = recurrence(db, insp)
    d["recurrence"] = rec
    d["guidance"] = guidance(insp.review_label or insp.label, insp.severity, insp.needs_review, rec)
    return d
