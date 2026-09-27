"""Evaluate any model directory (ONNX/INT8/challenger) on a labelled folder dataset.

    python scripts/evaluate.py --model-dir models/wafer-ensemble --data-root dataset --split test
    python scripts/evaluate.py --model-dir models/wafer-ensemble --data-root my_new_lot   # whole folder

--split uses the same leakage-safe split (same seed) as training, so "test" is the
untouched held-out set. Prints accuracy, macro-F1, per-class recall and latency.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # run from anywhere

import argparse
import json
import time

import numpy as np

from waferguard.data import dataset as ds
from waferguard.inference.engine import InferenceEngine
from waferguard.training.train import metrics  # sklearn-only helper, no TensorFlow import at module level


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--split", choices=["train", "val", "test", "all"], default="all")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tta", choices=["on", "off"], default=None)
    ap.add_argument("--providers", default="auto")
    ap.add_argument("--cache-dir", default=".cache/preprocess")
    ap.add_argument("--json", help="write the metrics here")
    a = ap.parse_args()
    eng = InferenceEngine(a.model_dir, a.providers, None if a.tta is None else a.tta == "on")
    idx = ds.scan(a.data_root, eng.classes)
    x = ds.load_arrays(idx.paths, eng.input_size, a.cache_dir)
    y = np.array(idx.labels)
    if a.split != "all":
        sel = ds.leakage_safe_split(x, y, a.seed)[a.split]
        x, y = x[sel], y[sel]
    t = time.perf_counter()
    probs = np.concatenate([eng.predict_tensors(x[i:i + 128]) for i in range(0, len(x), 128)])
    ms = (time.perf_counter() - t) / len(x) * 1000
    m = metrics(y, probs, eng.classes)
    m["latency_ms_per_wafer"] = ms
    print(f"{len(y)} wafers | accuracy {m['accuracy']:.4f} | macro-F1 {m['macro_f1']:.4f} | {ms:.2f} ms/wafer")
    for c, v in m["per_class"].items():
        print(f"  {c:10s} recall {v['recall']:.3f}  precision {v['precision']:.3f}  n={v['support']}")
    if a.json:
        json.dump(m, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
