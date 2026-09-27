"""Batch worker process: consumes the Redis job queue.

    WG_REDIS_URL=redis://redis:6379/0 python -m waferguard.api.worker

Scale horizontally by running more workers (docker compose --scale worker=4, or the k8s HPA).
"""
from __future__ import annotations

import logging
import signal
import threading

from waferguard.api.main import configure_logging
from waferguard.api.services.jobs import consume_forever
from waferguard.api.settings import get_settings
from waferguard.api.state import AppState


def main():  # pragma: no cover - long-running process
    settings = get_settings()
    configure_logging(settings)
    if not settings.redis_url:
        raise SystemExit("WG_REDIS_URL is required for a standalone worker")
    state = AppState(settings, with_queue=False)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.getLogger("waferguard").info("worker ready (model %s)", state.models.champion and state.models.champion.version)
    consume_forever(state, state.redis, stop)


if __name__ == "__main__":
    main()
