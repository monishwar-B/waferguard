import json
import os

import numpy as np
import pytest

from waferguard.data import synthetic
from waferguard.data.preprocess import levels_to_tensor
from waferguard.inference import features, localization
from waferguard.inference.engine import InferenceEngine, ModelLoadError, select_providers
from waferguard.inference.severity import SeverityPolicy, grade
from waferguard.taxonomy import WM811K_CLASSES, display_name

from .conftest import REAL_MODEL


def test_features_shape_and_signal():
    rng = np.random.default_rng(0)
    c = features.extract(levels_to_tensor(synthetic.make("Center", 48, rng)))
    e = features.extract(levels_to_tensor(synthetic.make("Edge-Ring", 48, rng)))
    n = features.extract(levels_to_tensor(np.where(synthetic.make("none", 48, rng) > 0, 1, 0).astype(np.uint8)))
    names = features.feature_names()
    assert c.shape == (len(names),)
    i_center, i_edge = names.index("center_disc_fail"), names.index("edge_band_fail")
    assert c[i_center] > e[i_center] and e[i_edge] > c[i_edge]
    assert n[names.index("fail_ratio")] == 0 and n[names.index("n_clusters")] == 0
    assert features.extract_batch(np.stack([levels_to_tensor(synthetic.make("Local", 48, rng))] * 2)).shape[0] == 2


def test_localization_regions_and_render():
    rng = np.random.default_rng(3)
    lv = synthetic.make("Local", 60, rng)
    regions, mask = localization.find_regions(lv, "Local")
    assert regions and regions[0]["area_px"] > 0 and mask.sum() > 0
    x, y, w, h = regions[0]["bbox"]
    assert 0 <= x < 60 and 0 <= y < 60 and w > 0 and h > 0
    assert localization.find_regions(lv, "none")[0] == []
    whole, _ = localization.find_regions(synthetic.make("Random", 60, rng), "Random")
    assert whole[0]["kind"] == "distributed"
    assert localization.find_regions(np.ones((10, 10), np.uint8), "Center")[0] == []
    img = localization.render(lv, regions, "Local", "Minor", 0.9)
    assert img.width >= 448
    assert localization.render(lv, regions, "Local", "Major", 0.9, source=synthetic.to_rgb(lv, scale=1)).width >= 448
    assert localization.render(lv, [], "none", "None", 0.5, source=(lv * 50).astype(np.uint16)).height >= 448
    assert len(localization.to_png_b64(img)) > 100 and len(localization.mask_png_b64(mask)) > 10


def test_severity_policy():
    assert grade("none", 0.99, 0.0)["severity"] == "None"
    assert grade("none", 0.99, 0.2)["severity"] == "Major"
    assert grade("none", 0.99, 0.5)["severity"] == "Critical"
    assert grade("Local", 0.95, 0.03)["severity"] == "Minor"
    assert grade("Local", 0.95, 0.15)["severity"] == "Major"
    assert grade("Center", 0.95, 0.35)["severity"] == "Critical"
    assert grade("Near-full", 0.99, 0.9)["severity"] == "Critical"
    g = grade("Scratch", 0.3, 0.01)
    assert g["needs_review"] and g["uncertain"] and g["severity"] == "Major"
    p = SeverityPolicy.from_dict({"review_confidence": 0.5, "base": {"Local": "Major"}})
    assert grade("Local", 0.6, 0.0, p) == {"severity": "Major", "needs_review": False, "uncertain": False,
                                           "reasons": ["pattern 'Local' baseline Major"]}
    assert grade("unknown-class", 0.9, 0.0)["severity"] == "Minor"
    assert display_name("Edge-Ring") == "Edge ring" and display_name("custom") == "custom"


def test_engine_tiny_model(tiny_model_dir):
    eng = InferenceEngine(tiny_model_dir)
    assert eng.classes == WM811K_CLASSES and "CPUExecutionProvider" in eng.providers
    rng = np.random.default_rng(42)
    labels = ["Center", "Edge-Ring", "Near-full", "none", "Donut"]
    imgs = [synthetic.to_rgb(synthetic.make(l, 48, rng)) for l in labels]
    res = eng.analyze_batch(imgs)
    assert sum(r["label"] == l for r, l in zip(res, labels)) >= 4
    one = eng.analyze(imgs[0], tta=True)
    assert abs(sum(one["probabilities"].values()) - 1) < 1e-6 and len(one["top3"]) == 3
    assert one["input_domain"] == "wafer_map_rgb" and one["domain_warning"] is None
    assert eng.analyze(imgs[0], localize=False)["regions"] == []
    info = eng.info()
    assert info["version"] == "test-1" and info["members"][0]["weight"] == 1.0


def test_engine_errors(tmp_path, tiny_model_dir):
    with pytest.raises(ModelLoadError):
        InferenceEngine(str(tmp_path))
    m = json.load(open(os.path.join(tiny_model_dir, "manifest.json")))
    (tmp_path / "manifest.json").write_text(json.dumps({**m, "members": [{"name": "x", "type": "onnx", "file": "x.onnx"}]}))
    with pytest.raises(ModelLoadError):
        InferenceEngine(str(tmp_path))
    (tmp_path / "x.onnx").write_bytes(b"")
    (tmp_path / "manifest.json").write_text(json.dumps({**m, "members": [{"name": "x", "type": "bogus", "file": "x.onnx"}]}))
    with pytest.raises(ModelLoadError):
        InferenceEngine(str(tmp_path))
    assert select_providers("CPUExecutionProvider") == ["CPUExecutionProvider"]
    assert select_providers(["NoSuchProvider"]) == ["CPUExecutionProvider"]


@pytest.mark.skipif(not os.path.exists(os.path.join(REAL_MODEL, "manifest.json")), reason="trained model not present")
def test_real_model_loads_and_predicts():
    eng = InferenceEngine(REAL_MODEL)
    assert len(eng.members) >= 2 and eng.manifest["test_metrics"]["accuracy"] > 0.5
    r = eng.analyze(synthetic.to_rgb(synthetic.make("Near-full", 48, np.random.default_rng(0))))
    assert r["label"] in eng.classes and 0 <= r["confidence"] <= 1


def test_cli_predict_and_info(tmp_path, tiny_model_dir, capsys):
    from PIL import Image

    from waferguard.cli import main
    for i, l in enumerate(["Center", "none"]):
        Image.fromarray(synthetic.to_rgb(synthetic.make(l, 40, np.random.default_rng(i)))).save(tmp_path / f"{l}.png")
    out = tmp_path / "res.csv"
    main(["predict", str(tmp_path), "--out", str(out), "--model-dir", tiny_model_dir, "--tta", "off"])
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 3 and lines[0].startswith("file,label")
    main(["predict", str(tmp_path / "Center.png"), "--model-dir", tiny_model_dir])
    main(["info", "--model-dir", tiny_model_dir])
    assert "test-1" in capsys.readouterr().out
