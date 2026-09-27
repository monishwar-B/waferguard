"""Convert the gradient-boosting member of a model folder to the portable format.

    python scripts/convert_gbm.py models/wafer-ensemble

Run it once on the machine where the model was trained (where scikit-learn can
read the .joblib file). Afterwards the model loads on any library version,
including a freshly built Docker image.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # run from anywhere

import json
import os
import sys

import joblib
import numpy as np

from waferguard.inference.portable_gbm import PortableHGB, export_hgb


def main(model_dir: str) -> None:
    man_path = os.path.join(model_dir, "manifest.json")
    man = json.load(open(man_path))
    changed = False
    for m in man["members"]:
        if m["type"] != "sklearn":
            continue
        src = os.path.join(model_dir, m["file"])
        dst = os.path.splitext(src)[0] + ".npz"
        clf = joblib.load(src)
        export_hgb(clf, dst)
        x = np.random.default_rng(0).normal(size=(256, clf.n_features_in_))
        diff = float(np.abs(PortableHGB(dst).predict_proba(x) - clf.predict_proba(x)).max())
        if diff > 1e-9:
            raise SystemExit(f"verification failed for {m['name']}: max difference {diff}")
        m["type"], m["file"] = "portable_gbm", os.path.basename(dst)
        changed = True
        print(f"converted {m['name']} -> {os.path.basename(dst)} (verified, max difference {diff:.1e})")
    if changed:
        json.dump(man, open(man_path, "w"), indent=2)
        print("manifest updated")
    else:
        print("nothing to convert: this model is already portable")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models/wafer-ensemble")
