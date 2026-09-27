"""Real-time event bus.

Events (inspection.created, job.progress, alert.raised, camera.status ...) are
published from any thread. Locally they fan out to every connected WebSocket.
With Redis configured they are also published on ``wg:events`` so that
separate worker processes / API replicas all reach every browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid

log = logging.getLogger("waferguard.events")
CHANNEL = "wg:events"


class EventBus:
    def __init__(self, redis_client=None):
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.redis = redis_client
        self._origin = uuid.uuid4().hex  # ignore our own messages echoed back by Redis
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self.redis is not None and self._listener is None:
            self._listener = threading.Thread(target=self._listen, name="wg-redis-events", daemon=True)
            self._listener.start()

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def _deliver(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            if q.full():  # slow client: drop its oldest event rather than block the line
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
            q.put_nowait(event)

    def publish(self, type_: str, data: dict) -> None:
        event = {"type": type_, "data": data}
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._deliver, event)
        if self.redis is not None:
            try:
                self.redis.publish(CHANNEL, json.dumps({**event, "_origin": self._origin}, default=str))
            except Exception as exc:  # noqa: BLE001
                log.warning("redis publish failed: %s", exc)

    def _listen(self) -> None:  # pragma: no cover - exercised only with a live Redis
        while not self._stop.is_set():
            try:
                ps = self.redis.pubsub(ignore_subscribe_messages=True)
                ps.subscribe(CHANNEL)
                for msg in ps.listen():
                    if self._stop.is_set():
                        break
                    ev = json.loads(msg["data"])
                    if ev.pop("_origin", None) == self._origin:
                        continue
                    if self._loop is not None:
                        self._loop.call_soon_threadsafe(self._deliver, ev)
            except Exception as exc:  # noqa: BLE001
                log.warning("redis event listener error, retrying: %s", exc)
                self._stop.wait(2.0)

    def close(self) -> None:
        self._stop.set()
