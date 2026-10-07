"""Where wafer images are kept.

* ``disk``  files under ``WG_STORAGE_DIR``. Fast; right for on-prem servers, Docker volumes and the desktop app.
* ``db``    file contents live in the database (table ``stored_files``). Use this on hosts whose disk is
            erased on every restart or redeploy (Render / Railway free tiers), so history keeps its images.
* ``auto``  (default) ``db`` when the database is PostgreSQL, ``disk`` when it is SQLite.

Callers keep the string returned by :func:`put` in ``Inspection.image_path`` / ``annotated_path`` /
``mask_path`` and read it back with :func:`get`. A reference starting with ``db:`` is a database row,
anything else is a file path, so old records keep working after the mode is changed.
"""
from __future__ import annotations

import mimetypes
import os

from waferguard.api.db import StoredFile

_db = None
_mode = "disk"


def resolve_mode(setting: str, database_url: str) -> str:
    setting = (setting or "auto").lower()
    if setting in ("disk", "db"):
        return setting
    return "disk" if database_url.startswith("sqlite") else "db"


def configure(db, mode: str) -> None:
    global _db, _mode
    _db, _mode = db, mode


def mode() -> str:
    return _mode


def put(name: str, data: bytes, disk_dir: str | None = None, session=None) -> str:
    """Store ``data`` and return the reference to save on the inspection record.

    In ``db`` mode pass the caller's open ``session`` so the image is saved in the same transaction as the
    inspection row (all or nothing, and no second writer fighting SQLite for the lock).
    """
    if _mode == "db" and _db is not None:
        ref = f"db:{name}"
        own = session is None
        s = _db.Session() if own else session
        try:
            row = s.get(StoredFile, ref)
            if row is None:
                s.add(StoredFile(ref=ref, data=data))
            else:
                row.data = data
            if own:
                s.commit()
        finally:
            if own:
                s.close()
        return ref
    if not disk_dir:
        raise ValueError("disk storage needs a directory")
    os.makedirs(disk_dir, exist_ok=True)
    path = os.path.join(disk_dir, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def get(ref: str | None) -> bytes | None:
    """The stored bytes, or None when the file is gone (for example a disk that was wiped)."""
    if not ref:
        return None
    if ref.startswith("db:"):
        if _db is None:
            return None
        with _db.Session() as s:
            row = s.get(StoredFile, ref)
            return bytes(row.data) if row is not None else None
    try:
        with open(ref, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def media_type(ref: str) -> str:
    return mimetypes.guess_type(ref)[0] or "application/octet-stream"
