"""Regression tests for problems found while testing the full PostgreSQL + Redis deployment."""
import json
import os
import shutil
import subprocess
import sys
import threading

import joblib
import numpy as np
import pytest
import redis

from waferguard.inference.engine import InferenceEngine, ModelLoadError
from waferguard.inference.portable_gbm import PortableHGB, export_hgb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_portable_gbm_matches_sklearn(tiny_model_dir, tmp_path):
    clf = joblib.load(os.path.join(tiny_model_dir, "gbm.joblib"))
    export_hgb(clf, str(tmp_path / "g.npz"))
    x = np.random.default_rng(1).normal(size=(300, clf.n_features_in_))
    x[::7, 3] = np.nan  # missing values follow the trained default direction
    assert np.abs(PortableHGB(str(tmp_path / "g.npz")).predict_proba(x) - clf.predict_proba(x)).max() < 1e-9


def test_convert_script_and_engine_prefers_portable(tiny_model_dir, tmp_path):
    d = tmp_path / "m"
    shutil.copytree(tiny_model_dir, d)
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "convert_gbm.py"), str(d)],
                         capture_output=True, text=True, check=True).stdout
    assert "verified" in out
    man = json.load(open(d / "manifest.json"))
    assert man["members"][0]["type"] == "portable_gbm"
    eng = InferenceEngine(str(d))
    assert isinstance(eng.members[0].runner, PortableHGB)
    again = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "convert_gbm.py"), str(d)],
                           capture_output=True, text=True, check=True).stdout
    assert "already portable" in again


def test_unloadable_pickle_gives_actionable_error(tiny_model_dir, tmp_path):
    d = tmp_path / "bad"
    shutil.copytree(tiny_model_dir, d)
    (d / "gbm.joblib").write_bytes(b"not a pickle")
    with pytest.raises(ModelLoadError, match="convert_gbm.py"):
        InferenceEngine(str(d))


def test_shipped_models_are_portable():
    for name in ("wafer-ensemble", "wafer-ensemble-int8"):
        man = json.load(open(os.path.join(ROOT, "models", name, "manifest.json")))
        assert all(m["type"] in ("onnx", "portable_gbm") for m in man["members"]), name


def test_worker_survives_redis_timeouts_and_disconnects(settings):
    from waferguard.api.services.jobs import consume_forever
    from waferguard.api.state import AppState

    state = AppState(settings, with_queue=False)
    stop = threading.Event()
    calls = {"n": 0}

    class Flaky:
        def blpop(self, key, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise redis.exceptions.TimeoutError("idle read timeout")
            if calls["n"] == 2:
                raise redis.exceptions.ConnectionError("redis restarted")
            stop.set()
            return None

    t = threading.Thread(target=consume_forever, args=(state, Flaky(), stop, 1))
    t.start()
    t.join(10)
    assert not t.is_alive() and calls["n"] == 3
    state.shutdown()


def test_bootstrap_admin_tolerates_concurrent_creation(settings):
    from waferguard.api.state import AppState
    a = AppState(settings, with_queue=False)
    b = AppState(settings, with_queue=False)  # second process against the same database
    from sqlalchemy import func, select

    from waferguard.api.db import User
    with b.db.Session() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 1
    a.shutdown(); b.shutdown()


def test_compose_and_dockerfile_invariants():
    import yaml
    c = yaml.safe_load(open(os.path.join(ROOT, "docker-compose.yml")))
    assert c["services"]["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert "healthcheck" in c["services"]["api"]
    df = open(os.path.join(ROOT, "deploy", "docker", "Dockerfile")).read()
    assert "mkdir -p /data/images" in df and "USER wg" in df
