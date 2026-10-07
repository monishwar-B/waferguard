"""History must survive a restart even when the host's disk is erased (Render / Railway free tiers)."""
import os

from fastapi.testclient import TestClient

from waferguard.api.main import create_app
from waferguard.api.settings import Settings

from .conftest import png_bytes, token


def _files(folder) -> list[str]:
    return [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs]


def test_history_and_images_survive_restart_with_db_storage(settings, tmp_path):
    s = settings.model_copy(update={"image_store": "db"})
    with TestClient(create_app(s)) as c:
        h = token(c)
        r = c.post("/api/v1/inspections", headers=h, files={"file": ("w.png", png_bytes("Edge-Ring"), "image/png")})
        assert r.status_code == 200, r.text
        iid = r.json()["id"]
        ready = c.get("/ready").json()
        assert ready["image_store"] == "db" and ready["database_kind"] == "sqlite"
    assert _files(tmp_path / "images") == []          # nothing needs the (erasable) disk

    # "restart": a brand-new app process on the same database
    with TestClient(create_app(s)) as c2:
        h = token(c2)
        assert c2.get(f"/api/v1/inspections/{iid}", headers=h).status_code == 200
        for kind in ("annotated", "source"):
            img = c2.get(f"/api/v1/inspections/{iid}/image/{kind}", headers=h)
            assert img.status_code == 200 and img.content.startswith(b"\x89PNG"), kind
        assert c2.get(f"/api/v1/inspections/{iid}/diemap", headers=h).status_code == 200
        pdf = c2.get(f"/api/v1/export/inspections/{iid}/report.pdf", headers=h)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        z = c2.get("/api/v1/export/images.zip", headers=h)
        assert z.status_code == 200 and len(z.content) > 500


def test_disk_storage_is_still_the_default_for_sqlite(client, admin, tmp_path):
    assert client.get("/ready").json()["image_store"] == "disk"
    r = client.post("/api/v1/inspections", headers=admin, files={"file": ("w.png", png_bytes("Center"), "image/png")})
    assert r.status_code == 200
    assert len(_files(tmp_path / "images")) >= 2


def test_hosted_postgres_urls_get_the_psycopg_driver():
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url="postgresql://u:p@h/db?sslmode=require").database_url.startswith("postgresql+psycopg://")
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_blank_database_url_falls_back_to_the_default():
    assert Settings(database_url="  ").database_url.startswith("sqlite:///")
