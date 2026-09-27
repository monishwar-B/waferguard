"""End-to-end training pipeline.

    python -m waferguard.training.train --config deploy/configs/train.yaml

Stages
  1. index + preprocess the folder-per-class dataset (cached)
  2. leakage-safe, group-aware train/val/test split (duplicates removed)
  3. train every CNN member with augmentation, focal loss, cosine LR and
     per-epoch hard negative mining; export each to ONNX
  4. train the gradient-boosting member on geometry features
  5. optional semi-supervised round: pseudo-label an unlabeled folder with the
     ensemble and fine-tune on confident predictions
  6. fit ensemble voting weights on the validation split (with 8-way TTA)
  7. evaluate on the untouched test split, write metrics + model manifest,
     log everything to MLflow when it is installed
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import time

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import numpy as np
import yaml

from waferguard.data import dataset as ds
from waferguard.data.io import is_supported, load_image_path
from waferguard.data.preprocess import MODEL_INPUT_SIZE, dihedral, preprocess
from waferguard.inference import features as feat
from waferguard.taxonomy import WM811K_CLASSES

DEFAULTS = {
    "data_root": "dataset",
    "classes": WM811K_CLASSES,
    "output_dir": "models/wafer-ensemble",
    "cache_dir": ".cache/preprocess",
    "input_size": MODEL_INPUT_SIZE,
    "leakage_safe_split": True,
    "seed": 42,
    "augment_profile": "wafer_map",
    "members": [
        {"arch": "wafernet", "width": 32, "seed": 1},
        {"arch": "wafernet", "width": 24, "seed": 2},
    ],
    "epochs": 40,
    "batch_size": 64,
    "lr": 2e-3,
    "weight_decay": 1e-4,
    "focal_gamma": 2.0,
    "label_smoothing": 0.05,
    "hard_negative_mining": {"enabled": True, "start_epoch": 5, "strength": 1.5, "top_frac": 0.2},
    "early_stopping_patience": 10,
    "gbm": {"enabled": True, "max_iter": 400, "learning_rate": 0.06},
    "tta": True,
    "pseudo_label": {"enabled": False, "unlabeled_dir": None, "threshold": 0.97, "weight": 0.5, "epochs": 8},
    "mlflow": {"enabled": True, "experiment": "waferguard", "tracking_uri": None},
    "model_version": None,
    "resume": True,
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | None, overrides: dict | None = None) -> dict:
    cfg = dict(DEFAULTS)
    if path:
        with open(path) as fh:
            cfg = _merge(cfg, yaml.safe_load(fh) or {})
    return _merge(cfg, overrides or {})


def tta_predict(predict_fn, x: np.ndarray, tta: bool = True) -> np.ndarray:
    if not tta:
        return predict_fn(x)
    return np.mean([predict_fn(dihedral(x, k)) for k in range(8)], axis=0)


def metrics(y: np.ndarray, probs: np.ndarray, classes: list[str]) -> dict:
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
    pred = probs.argmax(1)
    p, r, f, s = precision_recall_fscore_support(y, pred, labels=range(len(classes)), zero_division=0)
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "macro_f1": float(f1_score(y, pred, average="macro")),
        "nll": float(-np.mean(np.log(np.clip(probs[np.arange(len(y)), y], 1e-7, 1)))),
        "per_class": {c: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
                      for i, c in enumerate(classes)},
        "confusion_matrix": confusion_matrix(y, pred, labels=range(len(classes))).tolist(),
    }


def fit_vote_weights(member_probs: list[np.ndarray], y: np.ndarray, step: float = 0.05) -> list[float]:
    """Grid search on the simplex maximizing accuracy, ties broken by NLL."""
    n = len(member_probs)
    if n == 1:
        return [1.0]
    ticks = int(round(1 / step))
    best, best_key = None, None
    for combo in itertools.product(range(ticks + 1), repeat=n - 1):
        if sum(combo) > ticks:
            continue
        w = np.array(list(combo) + [ticks - sum(combo)], float) / ticks
        p = sum(wi * pi for wi, pi in zip(w, member_probs))
        acc = (p.argmax(1) == y).mean()
        nll = -np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-7, 1)))
        key = (round(acc, 5), -nll)
        if best_key is None or key > best_key:
            best, best_key = w, key
    return [float(v) for v in best]


def train_cnn(member: dict, cfg: dict, x_tr, y_tr, x_va, y_va, n_classes: int, extra=None, log=print,
              ckpt_dir: str | None = None):
    import keras

    from waferguard.training.architectures import build
    from waferguard.training.augment import AugmentedSequence, focal_loss, hard_negative_weights

    keras.utils.set_random_seed(member.get("seed", 0))
    kw = {k: v for k, v in member.items() if k not in ("arch", "seed", "name")}
    model = build(member["arch"], x_tr.shape[1:], n_classes, **kw)
    counts = np.bincount(y_tr, minlength=n_classes).astype(float)
    alpha = (counts.sum() / (n_classes * np.maximum(counts, 1)))
    alpha = (alpha / alpha.mean()).tolist()
    steps = int(np.ceil(len(x_tr) / cfg["batch_size"])) * cfg["epochs"]
    sched = keras.optimizers.schedules.CosineDecay(cfg["lr"], steps, warmup_target=None, alpha=0.02)
    opt = keras.optimizers.AdamW(sched, weight_decay=cfg["weight_decay"])
    model.compile(opt, focal_loss(cfg["focal_gamma"], alpha, cfg["label_smoothing"]), metrics=["accuracy"])
    x_fit, y_fit = x_tr, y_tr
    base_w = np.ones(len(x_tr), np.float32)
    if extra is not None:
        x_fit = np.concatenate([x_tr, extra[0]])
        y_fit = np.concatenate([y_tr, extra[1]])
        base_w = np.concatenate([base_w, np.full(len(extra[0]), extra[2], np.float32)])
    y_oh = keras.utils.to_categorical(y_fit, n_classes)
    seq = AugmentedSequence(x_fit, y_oh, cfg["batch_size"], cfg["augment_profile"], member.get("seed", 0), base_w.copy())
    hnm = cfg["hard_negative_mining"]
    best_acc, best_w, wait, history, start = -1.0, None, 0, [], 0
    ckpt = state_f = best_f = None
    if ckpt_dir:
        os.makedirs(ckpt_dir, exist_ok=True)
        ckpt = os.path.join(ckpt_dir, f"{member['name']}.epoch.weights.h5")
        best_f = os.path.join(ckpt_dir, f"{member['name']}.best.weights.h5")
        state_f = os.path.join(ckpt_dir, f"{member['name']}.state.json")
        if os.path.exists(state_f) and os.path.exists(ckpt):
            st = json.load(open(state_f))
            best_acc, wait, history, start = st["best_acc"], st["wait"], st["history"], st["epoch"]
            if os.path.exists(best_f):
                model.load_weights(best_f)
                best_w = model.get_weights()
            model.load_weights(ckpt)
            # restore the LR schedule position (Adam moments restart; harmless after a few steps)
            model.optimizer.build(model.trainable_variables)
            model.optimizer.iterations.assign(start * len(seq))
            log(f"    [{member['name']}] resumed after epoch {start} (best val_acc {best_acc:.4f})")
    for epoch in range(start, cfg["epochs"]):
        if wait >= cfg["early_stopping_patience"]:
            break
        t0 = time.time()
        h = model.fit(seq, epochs=epoch + 1, initial_epoch=epoch, verbose=0)
        pv = model.predict(x_va, batch_size=256, verbose=0)
        acc = float((pv.argmax(1) == y_va).mean())
        history.append({"epoch": epoch + 1, "loss": float(h.history["loss"][-1]), "val_acc": acc})
        log(f"    [{member['name']}] epoch {epoch + 1}/{cfg['epochs']} loss={h.history['loss'][-1]:.4f} "
            f"val_acc={acc:.4f} ({time.time() - t0:.0f}s)")
        if acc > best_acc:
            best_acc, best_w, wait = acc, model.get_weights(), 0
            if best_f:
                model.save_weights(best_f)
        else:
            wait += 1
            if wait >= cfg["early_stopping_patience"]:
                log(f"    early stop at epoch {epoch + 1}")
        if hnm["enabled"] and epoch + 1 >= hnm["start_epoch"]:
            ptr = model.predict(x_fit, batch_size=256, verbose=0)
            seq.weights = base_w * hard_negative_weights(y_fit, ptr, hnm["strength"], hnm["top_frac"])
        if ckpt:
            model.save_weights(ckpt)
            json.dump({"epoch": epoch + 1, "best_acc": best_acc, "wait": wait, "history": history}, open(state_f, "w"))
    model.set_weights(best_w)
    return model, history


def export_onnx(model, x_example: np.ndarray, path: str) -> None:
    model.predict(x_example[:2], verbose=0)
    model.export(path, format="onnx", verbose=False)


def train_gbm(cfg, f_tr, y_tr):
    from sklearn.ensemble import HistGradientBoostingClassifier
    g = cfg["gbm"]
    clf = HistGradientBoostingClassifier(max_iter=g["max_iter"], learning_rate=g["learning_rate"],
                                         early_stopping=True, validation_fraction=0.1,
                                         random_state=cfg["seed"], class_weight="balanced")
    return clf.fit(f_tr, y_tr)


def load_unlabeled(folder: str, size: int) -> np.ndarray:
    files = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if is_supported(f)]
    return np.stack([preprocess(load_image_path(p), size)[0] for p in files]) if files else np.zeros((0, size, size, 3), np.float32)


def run(cfg: dict, log=print) -> dict:
    t_start = time.time()
    out = cfg["output_dir"]
    os.makedirs(out, exist_ok=True)
    classes = list(cfg["classes"])
    k = len(classes)
    log(f"[1/7] indexing {cfg['data_root']}")
    index = ds.scan(cfg["data_root"], classes, seed=cfg["seed"])
    x_all = ds.load_arrays(index.paths, cfg["input_size"], cfg["cache_dir"], progress=True)
    y_all = np.array(index.labels)
    log("[2/7] splitting")
    if cfg["leakage_safe_split"]:
        sp = ds.leakage_safe_split(x_all, y_all, cfg["seed"])
        split_report = sp["report"]
    else:
        sp = {s: np.array([i for i, v in enumerate(index.splits) if v == s]) for s in ("train", "val", "test")}
        split_report = {"mode": "filename splits (may contain duplicates)"}
    x_tr, y_tr = x_all[sp["train"]], y_all[sp["train"]]
    x_va, y_va = x_all[sp["val"]], y_all[sp["val"]]
    x_te, y_te = x_all[sp["test"]], y_all[sp["test"]]
    log(f"      {split_report}")

    mlf = None
    if cfg["mlflow"]["enabled"]:
        try:
            import mlflow
            if cfg["mlflow"].get("tracking_uri"):
                mlflow.set_tracking_uri(cfg["mlflow"]["tracking_uri"])
            mlflow.set_experiment(cfg["mlflow"]["experiment"])
            mlflow.start_run()
            mlflow.log_params({k2: str(v) for k2, v in cfg.items() if k2 not in ("classes",)})
            mlf = mlflow
        except Exception as exc:  # noqa: BLE001
            log(f"      MLflow disabled ({exc.__class__.__name__}: {exc})")

    log("[3/7] training CNN members")
    members, keras_models, histories = [], {}, {}
    for i, m in enumerate(cfg["members"]):
        m = {**m, "name": m.get("name") or f"{m['arch']}_{i}"}
        ckpt = os.path.join(out, f"{m['name']}.keras")
        if cfg.get("resume") and os.path.exists(ckpt):
            import keras
            from waferguard.training.architectures import PatchEmbed
            log(f"    resuming: loading finished member {ckpt}")
            model = keras.models.load_model(ckpt, compile=False, custom_objects={"PatchEmbed": PatchEmbed})
            hist = []
        else:
            model, hist = train_cnn(m, cfg, x_tr, y_tr, x_va, y_va, k, log=log, ckpt_dir=out)
            model.save(ckpt)  # checkpoint so an interrupted run can resume
        keras_models[m["name"]] = model
        histories[m["name"]] = hist
        members.append({"name": m["name"], "type": "onnx", "file": f"{m['name']}.onnx", "arch": m["arch"],
                        "params": {k: v for k, v in m.items() if k not in ("name", "arch")}})

    gbm = None
    if cfg["gbm"]["enabled"]:
        log("[4/7] training gradient-boosting member on geometry features")
        import joblib
        f_tr, f_va, f_te = (feat.extract_batch(a) for a in (x_tr, x_va, x_te))
        gbm = train_gbm(cfg, f_tr, y_tr)
        from waferguard.inference.portable_gbm import export_hgb
        joblib.dump(gbm, os.path.join(out, "gbm_features.joblib"))      # for analysis / fine-tuning
        export_hgb(gbm, os.path.join(out, "gbm_features.npz"))           # what serving loads
        members.append({"name": "gbm_features", "type": "portable_gbm", "file": "gbm_features.npz"})

    pl = cfg["pseudo_label"]
    if pl["enabled"] and pl.get("unlabeled_dir"):
        log("[5/7] semi-supervised pseudo-labelling round")
        xu = load_unlabeled(pl["unlabeled_dir"], cfg["input_size"])
        if len(xu):
            pu = np.mean([mdl.predict(xu, verbose=0) for mdl in keras_models.values()], axis=0)
            keep = pu.max(1) >= pl["threshold"]
            log(f"      {keep.sum()}/{len(xu)} unlabeled images accepted at p>={pl['threshold']}")
            extra = (xu[keep], pu[keep].argmax(1), pl["weight"])
            ft_cfg = {**cfg, "epochs": pl["epochs"], "lr": cfg["lr"] * 0.2,
                      "hard_negative_mining": {**cfg["hard_negative_mining"], "enabled": False}}
            for m in cfg["members"]:
                name = m.get("name") or f"{m['arch']}_{cfg['members'].index(m)}"
                keras_models[name], _ = train_cnn({**m, "name": name}, ft_cfg, x_tr, y_tr, x_va, y_va, k, extra, log)
                keras_models[name].save(os.path.join(out, f"{name}.keras"))
            split_report["pseudo_labelled"] = int(keep.sum())
    else:
        log("[5/7] pseudo-labelling skipped (no unlabeled_dir)")

    log("[6/7] fitting ensemble weights on validation (TTA=%s)" % cfg["tta"])
    for name, model in keras_models.items():
        export_onnx(model, x_tr, os.path.join(out, f"{name}.onnx"))

    def member_probs(x, tta):
        ps = [tta_predict(lambda a, mm=mm: mm.predict(a, batch_size=256, verbose=0), x, tta) for mm in keras_models.values()]
        if gbm is not None:
            fx = feat.extract_batch(x)
            ps.append(gbm.predict_proba(fx))
        return ps

    pv = member_probs(x_va, cfg["tta"])
    weights = fit_vote_weights(pv, y_va)
    for mem, w in zip(members, weights):
        mem["weight"] = w

    log("[7/7] evaluating on held-out test split")
    pt_notta = member_probs(x_te, False)
    pt = member_probs(x_te, cfg["tta"]) if cfg["tta"] else pt_notta
    ens = sum(w * p for w, p in zip(weights, pt))
    ens_notta = sum(w * p for w, p in zip(weights, pt_notta))
    ablation = {m["name"]: metrics(y_te, p, classes)["accuracy"] for m, p in zip(members, pt_notta)}
    ablation.update({f"{m['name']}+tta": metrics(y_te, p, classes)["accuracy"]
                     for m, p in zip(members, pt) if m["type"] == "onnx"})
    ablation["ensemble_no_tta"] = metrics(y_te, ens_notta, classes)["accuracy"]
    test = metrics(y_te, ens, classes)
    test["ablation_accuracy"] = ablation

    data_hash = hashlib.sha1("".join(sorted(os.path.basename(p) for p in index.paths)).encode()).hexdigest()[:12]
    version = cfg.get("model_version") or dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d-%H%M")
    manifest = {
        "name": "waferguard-ensemble",
        "version": version,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "classes": classes,
        "input_size": cfg["input_size"],
        "input_layout": "NHWC one-hot planes [background, pass, fail], float32",
        "tta": bool(cfg["tta"]),
        "members": members,
        "dataset": {"root": os.path.abspath(cfg["data_root"]), "file_list_sha1": data_hash, "split": split_report},
        "test_metrics": {k2: v for k2, v in test.items() if k2 != "confusion_matrix"},
        "confusion_matrix": test["confusion_matrix"],
        "training_seconds": round(time.time() - t_start, 1),
    }
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(out, "training_history.json"), "w") as fh:
        json.dump(histories, fh, indent=2)
    if mlf:
        mlf.log_metrics({"test_accuracy": test["accuracy"], "test_macro_f1": test["macro_f1"]})
        mlf.log_artifacts(out)
        mlf.end_run()
    log(f"done: test accuracy {test['accuracy']:.4f}, macro-F1 {test['macro_f1']:.4f} -> {out}")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train the WaferGuard ensemble")
    ap.add_argument("--config", help="YAML config (see deploy/configs/train.yaml)")
    ap.add_argument("--data-root")
    ap.add_argument("--output-dir")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--unlabeled-dir", help="enable pseudo-labelling with this folder")
    args = ap.parse_args(argv)
    over = {}
    if args.data_root:
        over["data_root"] = args.data_root
    if args.output_dir:
        over["output_dir"] = args.output_dir
    if args.epochs:
        over["epochs"] = args.epochs
    if args.unlabeled_dir:
        over["pseudo_label"] = {"enabled": True, "unlabeled_dir": args.unlabeled_dir}
    run(load_config(args.config, over))


if __name__ == "__main__":
    main()
