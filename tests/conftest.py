import json
import os
import time

import joblib
import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from waferguard.data import synthetic
from waferguard.data.preprocess import levels_to_tensor
from waferguard.inference.features import extract_batch
from waferguard.taxonomy import WM811K_CLASSES

REAL_MODEL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "wafer-ensemble")


@pytest.fixture(scope="session")
def tiny_model_dir(tmp_path_factory):
    """A fast, dependency-light model (geometry-feature GBM) trained on synthetic maps."""
    d = tmp_path_factory.mktemp("tiny_model")
    levels, y = synthetic.dataset(WM811K_CLASSES, 30, seed=1)
    x = np.stack([levels_to_tensor(lv) for lv in levels])
    clf = HistGradientBoostingClassifier(max_iter=60, random_state=0).fit(extract_batch(x), y)
    joblib.dump(clf, d / "gbm.joblib")
    (d / "manifest.json").write_text(json.dumps({
        "name": "tiny", "version": "test-1", "classes": WM811K_CLASSES, "input_size": 64, "tta": False,
        "members": [{"name": "gbm", "type": "sklearn", "file": "gbm.joblib", "weight": 1.0}],
    }))
    return str(d)


@pytest.fixture()
def settings(tmp_path, tiny_model_dir):
    from waferguard.api.settings import Settings
    return Settings(database_url=f"sqlite:///{tmp_path / 'wg.db'}", storage_dir=str(tmp_path / "images"),
                    jwt_secret="test-secret", serve_frontend=False,
                    models={"champion_dir": tiny_model_dir, "providers": "auto"},
                    alerts={"min_samples": 5, "window": 10, "spike_rate": 0.5, "consecutive_defects": 4,
                            "cooldown_minutes": 0})


@pytest.fixture()
def client(settings):
    from fastapi.testclient import TestClient

    from waferguard.api.main import create_app
    app = create_app(settings)
    with TestClient(app) as c:
        c.wg = app.state.wg
        yield c


def token(client, user="admin", pw="admin123"):
    r = client.post("/api/v1/auth/token", data={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def admin(client):
    return token(client)


def make_user(client, admin_h, name, role):
    r = client.post("/api/v1/users", json={"username": name, "password": "password123", "role": role}, headers=admin_h)
    assert r.status_code == 201, r.text
    return token(client, name, "password123")


def png_bytes(label="Center", seed=0):
    import io

    from PIL import Image
    lv = synthetic.make(label, 48, np.random.default_rng(seed))
    buf = io.BytesIO()
    Image.fromarray(synthetic.to_rgb(lv)).save(buf, format="PNG")
    return buf.getvalue()


def wait_for(fn, timeout=20.0, interval=0.1):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("condition not met in time")
