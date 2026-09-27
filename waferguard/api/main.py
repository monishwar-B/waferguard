"""FastAPI application.

    uvicorn waferguard.api.main:app --host 0.0.0.0 --port 8000

OpenAPI docs at /docs (Swagger UI) and /redoc; schema at /openapi.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from waferguard import __version__
from waferguard.api.routers import analytics, auth, inspections, system
from waferguard.api.security import resolve_principal
from waferguard.api.services.observability import REQUESTS, WS_CLIENTS
from waferguard.api.settings import Settings, get_settings
from waferguard.api.state import AppState

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ready for Filebeat/Logstash (ELK) or Loki."""

    def format(self, record):
        d = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
             "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


def configure_logging(settings: Settings):
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if settings.log_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(settings.log_level)


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    if settings.jwt_secret in ("change-me-in-production", "please-set-a-long-random-secret") or len(settings.jwt_secret) < 32:
        logging.getLogger("waferguard").warning(
            "WG_JWT_SECRET is the default or shorter than 32 characters: anyone who knows it can forge logins. "
            "Set a long random value before exposing this server.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state.wg
        st.events.bind_loop(asyncio.get_running_loop())
        yield
        st.shutdown()

    app = FastAPI(
        title="WaferGuard API",
        version=__version__,
        description=("Wafer defect inspection: classification, localization, severity grading, SPC, alerting and "
                     "audit trail. Authenticate with `POST /api/v1/auth/token` (Bearer JWT) or an `X-API-Key` header "
                     "for MES/ERP integrations. Real-time events: WebSocket `/ws?token=...`."),
        lifespan=lifespan,
    )
    app.state.wg = state or AppState(settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=False)

    @app.middleware("http")
    async def timing(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        REQUESTS.labels(request.method, getattr(route, "path", "unmatched"), response.status_code).observe(time.perf_counter() - t0)
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover - safety net
        logging.getLogger("waferguard").exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    for r in (auth.router, inspections.router, analytics.router, system.router):
        app.include_router(r)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        token = websocket.query_params.get("token")
        try:
            principal = resolve_principal(websocket, token)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            await websocket.close(code=4401)
            return
        await websocket.accept()
        st = websocket.app.state.wg
        q = st.events.subscribe()
        WS_CLIENTS.set(st.events.subscriber_count)
        await websocket.send_json({"type": "hello", "data": {"user": principal.username, "role": principal.role}})
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                    await websocket.send_json(ev)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping", "data": {}})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            st.events.unsubscribe(q)
            WS_CLIENTS.set(st.events.subscriber_count)

    if settings.serve_frontend and os.path.isdir(STATIC_DIR) and os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = os.path.join(STATIC_DIR, path)
            if path and os.path.isfile(f) and os.path.commonpath([STATIC_DIR, os.path.abspath(f)]) == STATIC_DIR:
                return FileResponse(f)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app


def __getattr__(name):  # lazy module-level `app` for `uvicorn waferguard.api.main:app`
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
