"""Prometheus metrics and audit-trail helper."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

INSPECTIONS = Counter("wg_inspections_total", "Wafers inspected", ["label", "severity", "source", "variant"], registry=REGISTRY)
LATENCY = Histogram("wg_inference_latency_seconds", "Model latency per wafer", ["variant"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5), registry=REGISTRY)
ALERTS = Counter("wg_alerts_total", "Alerts raised", ["rule", "level"], registry=REGISTRY)
JOBS = Counter("wg_batch_jobs_total", "Batch jobs by final status", ["status"], registry=REGISTRY)
QUEUE_DEPTH = Gauge("wg_queue_depth", "Batch items waiting", registry=REGISTRY)
WS_CLIENTS = Gauge("wg_websocket_clients", "Connected WebSocket clients", registry=REGISTRY)
CAMERA_FPS = Gauge("wg_camera_fps", "Effective camera processing rate", ["camera"], registry=REGISTRY)
REQUESTS = Histogram("wg_http_request_seconds", "HTTP request latency", ["method", "route", "status"], registry=REGISTRY)


def audit(db, principal, action: str, resource: str | None = None, details: dict | None = None, ip: str | None = None):
    from waferguard.api.db import AuditLog
    with db.Session() as s:
        s.add(AuditLog(username=getattr(principal, "username", str(principal)), role=getattr(principal, "role", None),
                       action=action, resource=resource, details=details or {}, ip=ip))
        s.commit()
