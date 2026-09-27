"""Batch processing for high-throughput line integration.

Uploaded files are written to the shared storage volume, then a job message is
queued. Two back-ends with the same interface:

* ``LocalQueue``  thread pool inside the API process (desktop / single box)
* ``RedisQueue``  Redis list ``wg:jobs``; any number of ``python -m waferguard.api.worker``
                  processes (or k8s worker pods) consume it. Progress events flow
                  back to browsers through Redis pub/sub.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from waferguard.api.db import Job, utcnow
from waferguard.api.services.observability import JOBS, QUEUE_DEPTH
from waferguard.data.io import ImageFormatError, RawSpec, load_image_path

log = logging.getLogger("waferguard.jobs")
QUEUE_KEY = "wg:jobs"


def process_job(state, job_id: str, items: list[dict], meta: dict) -> None:
    db = state.db
    with db.Session() as s:
        job = s.get(Job, job_id)
        job.status = "running"
        s.commit()
    state.events.publish("job.progress", {"id": job_id, "status": "running", "done": 0, "failed": 0, "total": len(items)})
    done = failed = 0
    errors = []
    raw = RawSpec(**meta["raw_spec"]) if meta.get("raw_spec") else None
    for n, it in enumerate(items, 1):
        try:
            img = load_image_path(it["path"], raw)
            with open(it["path"], "rb") as fh:
                data = fh.read()
            m = {**meta, "filename": it["filename"], "source": "batch", "job_id": job_id,
                 "wafer_id": it.get("wafer_id") or os.path.splitext(it["filename"])[0]}
            state.inspections.inspect(img, m, raw_bytes=data, publish=False)
            done += 1
        except (ImageFormatError, OSError, ValueError) as exc:
            failed += 1
            errors.append(f"{it['filename']}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"{it['filename']}: {exc.__class__.__name__}: {exc}")
            log.exception("batch item failed")
        if n % 10 == 0 or n == len(items):
            with db.Session() as s:
                job = s.get(Job, job_id)
                job.done, job.failed = done, failed
                s.commit()
            state.events.publish("job.progress", {"id": job_id, "status": "running", "done": done,
                                                  "failed": failed, "total": len(items)})
    status = "done" if done else "failed"
    with db.Session() as s:
        job = s.get(Job, job_id)
        job.done, job.failed, job.status, job.finished_at = done, failed, status, utcnow()
        job.error = "\n".join(errors[:50]) or None
        s.commit()
    JOBS.labels(status).inc()
    state.events.publish("job.progress", {"id": job_id, "status": status, "done": done, "failed": failed,
                                          "total": len(items), "errors": errors[:10]})


class LocalQueue:
    kind = "local"

    def __init__(self, state, workers: int = 1):
        self.state = state
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wg-batch")
        self._pending = 0
        self._lock = threading.Lock()

    def submit(self, job_id: str, items: list[dict], meta: dict) -> None:
        with self._lock:
            self._pending += len(items)
            QUEUE_DEPTH.set(self._pending)

        def run():
            try:
                process_job(self.state, job_id, items, meta)
            finally:
                with self._lock:
                    self._pending -= len(items)
                    QUEUE_DEPTH.set(self._pending)

        return self.pool.submit(run)

    def depth(self) -> int:
        return self._pending

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class RedisQueue:
    kind = "redis"

    def __init__(self, redis_client):
        self.r = redis_client

    def submit(self, job_id: str, items: list[dict], meta: dict) -> None:
        self.r.rpush(QUEUE_KEY, json.dumps({"job_id": job_id, "items": items, "meta": meta}))
        QUEUE_DEPTH.set(self.depth())

    def depth(self) -> int:
        return int(self.r.llen(QUEUE_KEY))

    def shutdown(self):
        pass


def consume_forever(state, redis_client, stop: threading.Event | None = None, timeout: int = 5) -> None:
    """Worker loop: pop jobs from Redis and process them."""
    stop = stop or threading.Event()
    log.info("worker consuming %s", QUEUE_KEY)
    import redis as _redis
    while not stop.is_set():
        try:
            msg = redis_client.blpop(QUEUE_KEY, timeout=timeout)
        except _redis.exceptions.TimeoutError:
            continue  # idle read timeout on the blocking pop: just wait again
        except _redis.exceptions.ConnectionError as exc:
            log.warning("redis unavailable (%s); retrying in 2 s", exc)
            stop.wait(2.0)
            continue
        if not msg:
            continue
        payload = json.loads(msg[1])
        try:
            process_job(state, payload["job_id"], payload["items"], payload["meta"])
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", payload.get("job_id"))
