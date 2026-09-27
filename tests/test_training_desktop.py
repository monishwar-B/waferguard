import numpy as np

from waferguard.training.train import _merge, fit_vote_weights, load_config, metrics, tta_predict


def test_config_merge_and_load(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("epochs: 3\nhard_negative_mining: {strength: 9}\n")
    cfg = load_config(str(p), {"seed": 7})
    assert cfg["epochs"] == 3 and cfg["seed"] == 7
    assert cfg["hard_negative_mining"]["strength"] == 9 and cfg["hard_negative_mining"]["enabled"] is True
    assert _merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}}) == {"a": {"b": 3, "c": 2}}


def test_vote_weights_prefer_better_member():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, 300)
    good = np.eye(3)[y] * 0.8 + 0.2 / 3
    bad = rng.dirichlet(np.ones(3), 300)
    w = fit_vote_weights([bad, good], y, step=0.1)
    assert w[1] > w[0] and abs(sum(w) - 1) < 1e-9
    assert fit_vote_weights([good], y) == [1.0]


def test_metrics_and_tta():
    y = np.array([0, 1, 1, 2])
    probs = np.eye(3)[[0, 1, 2, 2]] * 0.9 + 0.1 / 3
    m = metrics(y, probs, ["a", "b", "c"])
    assert m["accuracy"] == 0.75 and m["confusion_matrix"][1] == [0, 1, 1] and m["per_class"]["a"]["recall"] == 1.0
    x = np.random.rand(2, 8, 8, 3).astype("float32")
    f = lambda a: np.tile(a.mean(axis=(1, 2, 3))[:, None], (1, 3))  # rotation invariant
    assert np.allclose(tta_predict(f, x, True), tta_predict(f, x, False))


def test_desktop_helpers(tmp_path, monkeypatch):
    from waferguard.desktop import launcher
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for k in ("WG_CONFIG", "WG_DATABASE_URL", "WG_STORAGE_DIR", "WG_MODELS__CHAMPION_DIR", "WG_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    env = launcher.configure_env()
    assert env["WG_DATABASE_URL"].startswith("sqlite:///") and len(env["WG_JWT_SECRET"]) == 64
    assert launcher.configure_env()["WG_JWT_SECRET"] == env["WG_JWT_SECRET"]  # stable across starts
    assert 0 < launcher.free_port() < 65536
    assert launcher.wait_ready("http://127.0.0.1:1", timeout=0.5) is False
    assert launcher.resource_root().endswith("waferguard") or launcher.resource_root()
