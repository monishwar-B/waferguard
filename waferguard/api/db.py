"""Database layer (SQLAlchemy 2.0).

PostgreSQL in production (``WG_DATABASE_URL=postgresql+psycopg://...``),
SQLite for the desktop build and tests. Images live on the storage volume;
the database keeps paths and all metadata (lot / wafer / equipment / operator).
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event,
                        func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(12))
    role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    operator: Mapped[str] = mapped_column(String(64), index=True)
    equipment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    lot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    wafer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recipe: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload|camera|batch|api
    filename: Mapped[str | None] = mapped_column(String(256))
    image_path: Mapped[str | None] = mapped_column(String(512))
    annotated_path: Mapped[str | None] = mapped_column(String(512))
    mask_path: Mapped[str | None] = mapped_column(String(512))
    label: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    die_count: Mapped[int] = mapped_column(Integer, default=0)
    probabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    input_domain: Mapped[str | None] = mapped_column(String(24))
    model_version: Mapped[str | None] = mapped_column(String(64))
    model_variant: Mapped[str] = mapped_column(String(16), default="champion")
    shadow: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    review_label: Mapped[str | None] = mapped_column(String(32))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    job = relationship("Job")

    @property
    def is_defective(self) -> bool:
        return (self.review_label or self.label) != "none"


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    rule: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16))
    equipment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(48), index=True)
    resource: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


class Database:
    def __init__(self, url: str):
        kw = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
            path = url.split("sqlite:///")[-1]
            if path and path != ":memory:":
                import os
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.engine = create_engine(url, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _wal(dbapi_conn, _):  # concurrent readers while the worker writes
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create tables. On PostgreSQL an advisory lock serialises this, so the API and several
        workers can start at the same moment against an empty database without colliding."""
        if self.engine.dialect.name == "postgresql":
            from sqlalchemy import text
            with self.engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(815301)"))
                Base.metadata.create_all(conn)
        else:
            Base.metadata.create_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as c:
            c.execute(func.now().select() if not str(self.engine.url).startswith("sqlite") else func.date().select())
        return True
