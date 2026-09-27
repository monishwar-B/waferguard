"""Training helpers that do not need TensorFlow (ensemble weighting, metrics, config)."""
import numpy as np

from waferguard.training.train import fit_vote_weights, load_config, metrics, tta_predict


def test_load_config_merges(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("epochs: 3\nhard_negative_mining: {strength: 9}\n")
    cfg = load_config(str(p), {"batch_size": 8})
    assert cfg["epochs"] == 3 and cfg["batch_size"] == 8
    assert cfg["hard_negative_mining"]["strength"] == 9 and cfg["hard_negative_mining"]["enabled"] is True
    assert load_config(None)["leakage_safe_split"] is True


def test_fit_vote_weights_prefers_the_better_member():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, 300)
    good = np.eye(3)[y] * 0.8 + 0.2 / 3
    bad = np.full((300, 3), 1 / 3) + rng.normal(0, 0.05, (300, 3))
    w = fit_vote_weights([bad, good], y, step=0.1)
    assert abs(sum(w) - 1) < 1e-9 and w[1] > w[0]
    assert fit_vote_weights([good], y) == [1.0]


def test_metrics_and_tta():
    y = np.array([0, 1, 2, 2])
    probs = np.eye(3)[[0, 1, 2, 1]]
    m = metrics(y, probs * 0.9 + 0.1 / 3, ["a", "b", "c"])
    assert m["accuracy"] == 0.75 and m["confusion_matrix"][2] == [0, 1, 1]
    x = np.random.rand(2, 8, 8, 3).astype("float32")
    f = lambda a: a.mean(axis=(1, 2))  # rotation-invariant -> TTA must equal plain
    assert np.allclose(tta_predict(f, x), f(x)) and np.allclose(tta_predict(f, x, False), f(x))
