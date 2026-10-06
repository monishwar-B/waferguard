import io
import os
import zipfile

import numpy as np
import pytest

from .conftest import make_user, png_bytes, token, wait_for


def _inspect(client, h, label="Center", seed=0, **meta):
    r = client.post("/api/v1/inspections", headers=h,
                    files={"file": (f"{label}_{seed}.png", png_bytes(label, seed), "image/png")},
                    data={"lot_id": "LOT1", "equipment_id": "EQ1", **meta})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_ready_metrics_docs(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["model"] and r.json()["database"]
    assert "wg_inspections_total" in client.get("/metrics").text
    spec = client.get("/openapi.json").json()
    assert "/api/v1/inspections" in spec["paths"] and "/api/v1/spc/p-chart" in spec["paths"]


def test_auth_and_rbac(client, admin):
    assert client.post("/api/v1/auth/token", data={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/api/v1/inspections").status_code == 401
    assert client.get("/api/v1/inspections", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=admin).json()["role"] == "Admin"
    op = make_user(client, admin, "op1", "Operator")
    eng = make_user(client, admin, "eng1", "Engineer")
    mgr = make_user(client, admin, "mgr1", "Manager")
    assert client.get("/api/v1/users", headers=op).status_code == 403
    assert client.get("/api/v1/jobs", headers=op).status_code == 403
    assert client.get("/api/v1/jobs", headers=eng).status_code == 200
    assert client.get("/api/v1/audit", headers=eng).status_code == 403
    assert client.get("/api/v1/audit", headers=mgr).status_code == 200
    assert client.post("/api/v1/users", json={"username": "op1", "password": "password123"}, headers=admin).status_code == 409
    assert client.post("/api/v1/users", json={"username": "x1", "password": "password123", "role": "God"}, headers=admin).status_code == 422
    r = client.patch("/api/v1/users/op1", json={"role": "Engineer", "full_name": "Op One", "password": "newpassword1"}, headers=admin)
    assert r.json()["role"] == "Engineer"
    assert client.patch("/api/v1/users/nobody", json={"active": False}, headers=admin).status_code == 404
    assert client.patch("/api/v1/users/admin", json={"active": False}, headers=admin).status_code == 400
    client.patch("/api/v1/users/op1", json={"active": False}, headers=admin)
    assert client.post("/api/v1/auth/token", data={"username": "op1", "password": "newpassword1"}).status_code == 401
    assert len(client.get("/api/v1/users", headers=admin).json()) == 4
    assert client.post("/api/v1/auth/password", json={"old_password": "bad", "new_password": "longenough1"}, headers=eng).status_code == 400
    assert client.post("/api/v1/auth/password", json={"old_password": "x", "new_password": "short"}, headers=eng).status_code == 422
    assert client.post("/api/v1/auth/password", json={"old_password": "password123", "new_password": "longenough1"}, headers=eng).json()["ok"]
    token(client, "eng1", "longenough1")


def test_expired_token(client):
    from waferguard.api.security import create_token
    t = create_token("admin", "Admin", "test-secret", -1)
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401 and r.json()["detail"] == "token expired"


def test_api_keys_for_mes(client, admin):
    r = client.post("/api/v1/api-keys", json={"name": "mes", "role": "Operator"}, headers=admin)
    key = r.json()["api_key"]
    kh = {"X-API-Key": key}
    out = _inspect(client, kh, "Edge-Ring", 1)
    assert out["source"] == "api" and out["operator"] == "apikey:mes"
    assert client.get("/api/v1/api-keys", headers=admin).json()[0]["prefix"] == key[:10]
    assert client.post("/api/v1/api-keys", json={"name": "x", "role": "Root"}, headers=admin).status_code == 422
    client.delete(f"/api/v1/api-keys/{r.json()['id']}", headers=admin)
    assert client.get("/api/v1/auth/me", headers=kh).status_code == 401
    assert client.delete("/api/v1/api-keys/999", headers=admin).status_code == 404


def test_inspection_lifecycle(client, admin):
    res = _inspect(client, admin, "Center", 0, wafer_id="W01", recipe="ETCH-7")
    assert res["label"] in client.wg.models.champion.classes
    assert res["severity"] in ("None", "Minor", "Major", "Critical") and "_levels" not in res
    iid = res["id"]
    detail = client.get(f"/api/v1/inspections/{iid}", headers=admin).json()
    assert detail["wafer_id"] == "W01" and detail["recipe"] == "ETCH-7" and detail["has_image"]
    for kind in ("source", "annotated", "mask"):
        r = client.get(f"/api/v1/inspections/{iid}/image/{kind}", headers=admin)
        assert r.status_code in (200, 404)
    tok = admin["Authorization"].split()[1]
    assert client.get(f"/api/v1/inspections/{iid}/image/annotated?token={tok}").status_code == 200
    assert client.get(f"/api/v1/inspections/{iid}/image/bogus", headers=admin).status_code == 404
    assert client.get("/api/v1/inspections/nope", headers=admin).status_code == 404
    # review
    assert client.post(f"/api/v1/inspections/{iid}/review", json={"label": "BAD"}, headers=admin).status_code == 422
    r = client.post(f"/api/v1/inspections/{iid}/review", json={"label": "Donut", "note": "ring visible"}, headers=admin)
    assert r.json()["review_label"] == "Donut" and r.json()["reviewed_by"] == "admin"
    assert client.post("/api/v1/inspections/nope/review", json={"label": "Donut"}, headers=admin).status_code == 404
    # list + filters
    _inspect(client, admin, "none", 3, lot_id="LOT2")
    lst = client.get("/api/v1/inspections?lot_id=LOT1", headers=admin).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == iid
    assert client.get("/api/v1/inspections?label=Donut", headers=admin).json()["total"] == 1
    q = "wafer_id=W0&severity=Major&operator=admin&source=upload&equipment_id=EQ1&since=2000-01-01T00:00:00&until=2999-01-01T00:00:00"
    assert client.get(f"/api/v1/inspections?{q}", headers=admin).status_code == 200
    assert client.get("/api/v1/inspections?needs_review=true", headers=admin).status_code == 200
    assert client.get("/api/v1/inspections?needs_review=false", headers=admin).status_code == 200


def test_upload_formats_and_errors(client, admin):
    lv = np.zeros((40, 40), np.uint8)
    lv[5:35, 5:35] = 1
    lv[15:25, 15:25] = 2
    buf = io.BytesIO()
    np.save(buf, lv)
    r = client.post("/api/v1/inspections", headers=admin, files={"file": ("map.npy", buf.getvalue())})
    assert r.status_code == 200 and r.json()["input_domain"] == "wafer_map_levels"
    raw = (lv * 100).astype(np.uint8).tobytes()
    r = client.post("/api/v1/inspections", headers=admin, files={"file": ("s.raw", raw)},
                    data={"raw_width": 40, "raw_height": 40, "raw_dtype": "uint8"})
    assert r.status_code == 200
    assert client.post("/api/v1/inspections", headers=admin, files={"file": ("x.png", b"junk")}).status_code == 415
    client.wg.settings.max_upload_mb = 0
    assert client.post("/api/v1/inspections", headers=admin, files={"file": ("x.png", b"123")}).status_code == 413


def test_browser_frame(client, admin):
    import base64
    b64 = "data:image/png;base64," + base64.b64encode(png_bytes("Scratch", 2)).decode()
    r = client.post("/api/v1/inspections/frame", json={"image_b64": b64, "equipment_id": "KIOSK", "store": False}, headers=admin)
    assert r.status_code == 200 and r.json()["source"] == "camera"
    assert client.post("/api/v1/inspections/frame", json={"image_b64": "!!"}, headers=admin).status_code == 415


def test_batch_job_and_exports(client, admin):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        for i, lab in enumerate(["Center", "Edge-Ring", "none"]):
            zf.writestr(f"wafers/{lab}_{i}.png", png_bytes(lab, i))
        zf.writestr("readme.txt", "skip")
        zf.writestr("wafers/", "")
    files = [("files", ("lot.zip", z.getvalue(), "application/zip")), ("files", ("extra.png", png_bytes("Local", 9), "image/png"))]
    r = client.post("/api/v1/jobs/batch", headers=admin, files=files, data={"lot_id": "LOTB", "equipment_id": "EQ9"})
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    assert r.json()["total"] == 4
    job = wait_for(lambda: (j := client.get(f"/api/v1/jobs/{jid}", headers=admin).json())["status"] == "done" and j)
    assert job["done"] == 4 and job["failed"] == 0
    assert client.get("/api/v1/jobs", headers=admin).json()[0]["id"] == jid
    assert client.get("/api/v1/jobs/nope", headers=admin).status_code == 404
    assert client.get(f"/api/v1/inspections?job_id={jid}", headers=admin).json()["total"] == 4
    assert client.post("/api/v1/jobs/batch", headers=admin, files=[("files", ("a.txt", b"x"))]).status_code == 422
    assert client.post("/api/v1/jobs/batch", headers=admin, files=[("files", ("a.zip", b"notzip"))]).status_code == 415
    # exports
    csv_text = client.get("/api/v1/export/inspections.csv?lot_id=LOTB", headers=admin).text
    assert csv_text.count("\n") == 5 and csv_text.startswith("id,created_at")
    zr = client.get("/api/v1/export/images.zip?lot_id=LOTB", headers=admin)
    names = zipfile.ZipFile(io.BytesIO(zr.content)).namelist()
    assert "inspections.csv" in names and any(n.startswith("annotated/") for n in names)
    one = client.get(f"/api/v1/inspections?lot_id=LOTB", headers=admin).json()["items"][0]["id"]
    client.post(f"/api/v1/inspections/{one}/review", json={"label": "Center"}, headers=admin)
    pdf = client.get(f"/api/v1/export/inspections/{one}/report.pdf", headers=admin)
    assert pdf.content[:4] == b"%PDF"
    s = client.get("/api/v1/export/summary.pdf?lot_id=LOTB", headers=admin)
    assert s.content[:4] == b"%PDF"
    op = make_user(client, admin, "opx", "Operator")
    for url in ("/api/v1/export/inspections.csv", "/api/v1/export/images.zip", "/api/v1/export/summary.pdf"):
        assert client.get(url, headers=op).status_code == 403


def test_spc_endpoints(client, admin):
    for i in range(6):
        _inspect(client, admin, ["Center", "none"][i % 2], i, lot_id=f"L{i // 2}")
    s = client.get("/api/v1/spc/summary", headers=admin).json()
    assert s["total"] == 6 and 0 <= s["defect_rate"] <= 1 and "cpk" in s["capability"]
    for g in ("lot", "hour", "day", "shift", "equipment"):
        pc = client.get(f"/api/v1/spc/p-chart?group_by={g}", headers=admin).json()
        assert pc["group_by"] == g and pc["points"]
    imr = client.get("/api/v1/spc/imr", headers=admin).json()
    assert len(imr["points"]) == 6 and "wafer_id" in imr["points"][0]
    assert client.get("/api/v1/spc/trend?group_by=day", headers=admin).json()[0]["total"] == 6
    assert client.get("/api/v1/spc/equipment", headers=admin).json()[0]["total"] == 6
    assert client.get("/api/v1/spc/p-chart?group_by=bogus", headers=admin).status_code == 422


def test_alerts_flow(client, admin):
    for i in range(6):
        _inspect(client, admin, "Near-full", i)
    alerts = client.get("/api/v1/alerts", headers=admin).json()
    assert alerts and {"defect_rate_spike", "consecutive_defects"} & {a["rule"] for a in alerts}
    aid = alerts[0]["id"]
    assert client.post(f"/api/v1/alerts/{aid}/ack", headers=admin).json()["acknowledged_by"] == "admin"
    assert client.post("/api/v1/alerts/9999/ack", headers=admin).status_code == 404
    assert all(a["id"] != aid for a in client.get("/api/v1/alerts?open_only=true", headers=admin).json())
    rules = client.get("/api/v1/alerts/rules", headers=admin).json()
    assert "spike_rate" in rules["rules"]
    assert client.put("/api/v1/alerts/rules", json={"spike_rate": 0.6}, headers=admin).json()["spike_rate"] == 0.6
    assert client.post("/api/v1/alerts/test", headers=admin).json()["rule"] == "test"
    eng = make_user(client, admin, "e2", "Engineer")
    assert client.put("/api/v1/alerts/rules", json={"spike_rate": 0.1}, headers=eng).status_code == 403


def test_audit_trail(client, admin):
    _inspect(client, admin, "Center", 5)
    log = client.get("/api/v1/audit", headers=admin).json()
    actions = {a["action"] for a in log}
    assert {"login", "inspect"} <= actions
    assert client.get("/api/v1/audit?action=inspect&username=admin", headers=admin).json()[0]["resource"]
    csv_text = client.get("/api/v1/audit.csv", headers=admin).text
    assert csv_text.startswith("id,ts,username")


def test_models_and_ab(client, admin, tiny_model_dir):
    info = client.get("/api/v1/models", headers=admin).json()
    assert info["ready"] and info["champion"]["version"] == "test-1"
    assert client.post("/api/v1/models/promote", headers=admin).status_code == 409
    r = client.put("/api/v1/models/ab", json={"mode": "shadow", "challenger_dir": tiny_model_dir}, headers=admin)
    assert r.json()["ab_mode"] == "shadow" and r.json()["challenger"]
    res = _inspect(client, admin, "Center", 1)
    assert client.get(f"/api/v1/inspections/{res['id']}", headers=admin).json()["shadow"]["agrees"] is True
    client.put("/api/v1/models/ab", json={"mode": "split", "split": 1.0}, headers=admin)
    assert _inspect(client, admin, "none", 2)["model_variant"] == "challenger"
    stats = client.get("/api/v1/models/ab/stats", headers=admin).json()
    assert stats["shadow"]["agreement"] == 1.0 and any(k.startswith("challenger") for k in stats["variants"])
    assert client.put("/api/v1/models/ab", json={"mode": "bogus"}, headers=admin).status_code == 422
    assert client.put("/api/v1/models/ab", json={"challenger_dir": "/nope"}, headers=admin).status_code == 422
    assert client.post("/api/v1/models/promote", headers=admin).json()["ab_mode"] == "off"
    assert client.post("/api/v1/models/reload", headers=admin).json()["ready"]


def test_model_unavailable(client, admin):
    client.wg.models.champion = None
    assert client.post("/api/v1/inspections", headers=admin, files={"file": ("a.png", png_bytes())}).status_code == 503
    assert client.get("/ready").status_code == 503
    assert client.post("/api/v1/cameras/start", json={"source": "0"}, headers=admin).status_code == 503


def test_camera_synthetic_stream_and_websocket(client, admin, tmp_path):
    from PIL import Image

    from waferguard.data import synthetic
    for i, lab in enumerate(["Center", "none", "Scratch"]):
        Image.fromarray(synthetic.to_rgb(synthetic.make(lab, 40, np.random.default_rng(i)))).save(tmp_path / f"{i}.png")
    tok = admin["Authorization"].split()[1]
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        assert ws.receive_json()["type"] == "hello"
        r = client.post("/api/v1/cameras/start", headers=admin,
                        json={"camera_id": "line1", "source": f"synthetic:{tmp_path}", "fps": 20, "equipment_id": "CAM-EQ"})
        assert r.status_code == 200, r.text
        seen = set()
        for _ in range(40):
            ev = ws.receive_json()
            seen.add(ev["type"])
            if "camera.frame" in seen and "inspection.created" in seen:
                break
        assert {"camera.frame", "inspection.created"} <= seen
    assert client.post("/api/v1/cameras/start", headers=admin, json={"camera_id": "line1", "source": f"synthetic:{tmp_path}"}).status_code == 409
    assert client.post("/api/v1/cameras/line1/fps?fps=2", headers=admin).json()["target_fps"] == 2
    assert client.post("/api/v1/cameras/line1/fps?fps=99", headers=admin).status_code == 422
    assert client.post("/api/v1/cameras/nope/fps?fps=2", headers=admin).status_code == 404
    st = client.get("/api/v1/cameras", headers=admin).json()[0]
    assert st["frames"] >= 1 and st["running"]
    stopped = client.post("/api/v1/cameras/line1/stop", headers=admin).json()
    assert not stopped["running"]
    assert client.post("/api/v1/cameras/nope/stop", headers=admin).status_code == 404
    assert client.post("/api/v1/cameras/start", headers=admin, json={"camera_id": "c2", "source": f"synthetic:{tmp_path / 'empty'}"}).status_code == 422
    assert isinstance(client.get("/api/v1/cameras/devices", headers=admin).json(), list)
    assert client.get(f"/api/v1/inspections?source=camera&equipment_id=CAM-EQ", headers=admin).json()["total"] >= 1


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=bad") as ws:
            ws.receive_json()


def test_settings_yaml_profile(tmp_path, monkeypatch):
    from waferguard.api.settings import Settings
    p = tmp_path / "p.yaml"
    p.write_text("profile: edge\nalerts:\n  spike_rate: 0.9\nmodels:\n  providers: CPUExecutionProvider\n")
    monkeypatch.setenv("WG_CONFIG", str(p))
    monkeypatch.setenv("WG_ALERTS__WINDOW", "77")
    s = Settings()
    assert s.profile == "edge" and s.alerts.spike_rate == 0.9 and s.alerts.window == 77
    assert s.models.providers == "CPUExecutionProvider"


def test_frontend_served(tmp_path, settings, monkeypatch):
    from fastapi.testclient import TestClient

    from waferguard.api import main
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "favicon.svg").write_text("<svg/>")
    monkeypatch.setattr(main, "STATIC_DIR", str(static))
    settings.serve_frontend = True
    with TestClient(main.create_app(settings)) as c:
        assert "app" in c.get("/").text and "app" in c.get("/spc").text
        assert c.get("/favicon.svg").text == "<svg/>"


@pytest.mark.parametrize("profile", ["onprem", "cloud", "edge-jetson", "edge-openvino", "desktop"])
def test_shipped_profiles_validate(profile, monkeypatch):
    from waferguard.api.settings import Settings
    monkeypatch.setenv("WG_CONFIG", f"deploy/configs/{profile}.yaml")
    s = Settings()
    assert s.profile == profile and s.alerts.window > 0


def test_history_survives_restart(settings):
    from fastapi.testclient import TestClient

    from waferguard.api.main import create_app
    with TestClient(create_app(settings)) as c1:
        h = token(c1)
        rid = _inspect(c1, h, "Donut", 3)["id"]
    with TestClient(create_app(settings)) as c2:  # same database + storage, as after reopening the app
        h = token(c2)
        body = c2.get("/api/v1/inspections", headers=h).json()
        assert body["total"] == 1 and body["items"][0]["id"] == rid
        assert c2.get(f"/api/v1/inspections/{rid}/image/annotated", headers=h).status_code == 200
