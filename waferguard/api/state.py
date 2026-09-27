"""Wires the services together. Used by the API process, the Redis worker and the desktop app."""
from __future__ import annotations

import logging

from sqlalchemy import select

from waferguard.api.db import Database, User
from waferguard.api.security import hash_password
from waferguard.api.services.alerts import AlertEngine
from waferguard.api.services.events import EventBus
from waferguard.api.services.inspections import InspectionService
from waferguard.api.services.models import ModelRegistry
from waferguard.inference.severity import SeverityPolicy

log = logging.getLogger("waferguard")


class AppState:
    def __init__(self, settings, redis_client=None, with_queue: bool = True):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.db.create_all()
        self._bootstrap_admin()
        if redis_client is None and settings.redis_url:
            import redis
            redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self.redis = redis_client
        self.events = EventBus(redis_client)
        self.models = ModelRegistry(settings, SeverityPolicy.from_dict(settings.severity))
        self.alerts = AlertEngine(self.db, settings, self.events)
        self.inspections = InspectionService(self)
        self.queue = None
        if with_queue:
            from waferguard.api.services.jobs import LocalQueue, RedisQueue
            self.queue = RedisQueue(redis_client) if redis_client is not None else LocalQueue(self)
        from waferguard.api.services.camera import CameraManager
        self.cameras = CameraManager(self)

    def _bootstrap_admin(self):
        from sqlalchemy.exc import IntegrityError
        with self.db.Session() as s:
            if s.scalar(select(User).limit(1)) is None:
                s.add(User(username=self.settings.bootstrap_admin_user, full_name="Administrator", role="Admin",
                           password_hash=hash_password(self.settings.bootstrap_admin_password)))
                try:
                    s.commit()
                    log.warning("created bootstrap admin user %r - change its password", self.settings.bootstrap_admin_user)
                except IntegrityError:  # another process created it at the same moment
                    s.rollback()

    def shutdown(self):
        self.cameras.stop_all()
        if self.queue:
            self.queue.shutdown()
        self.events.close()
