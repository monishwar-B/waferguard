"""Hyperparameter optimisation with Optuna.

    python -m waferguard.training.tune --config deploy/configs/train.yaml --trials 30 --epochs 12

Each trial trains one WaferNet on the leakage-safe training split and is scored by
validation accuracy (the test split is never touched). Unpromising trials are
pruned after a few epochs (median pruner). The best parameters are written to
``<output_dir>/best_hparams.yaml`` for you to merge into the training config.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import yaml

from waferguard.data import dataset as ds
from waferguard.training.train import load_config


def main(argv=None):
    import optuna

    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--data-root")
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--storage", help="e.g. sqlite:///optuna.db to resume / run in parallel")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, {"data_root": a.data_root} if a.data_root else {})
    idx = ds.scan(cfg["data_root"], cfg["classes"], seed=cfg["seed"])
    x = ds.load_arrays(idx.paths, cfg["input_size"], cfg["cache_dir"])
    y = np.array(idx.labels)
    sp = ds.leakage_safe_split(x, y, cfg["seed"])
    x_tr, y_tr, x_va, y_va = x[sp["train"]], y[sp["train"]], x[sp["val"]], y[sp["val"]]

    def objective(trial):
        import keras

        from waferguard.training.architectures import wafernet
        from waferguard.training.augment import AugmentedSequence, focal_loss
        p = {
            "width": trial.suggest_categorical("width", [16, 24, 32, 48]),
            "dropout": trial.suggest_float("dropout", 0.1, 0.5),
            "lr": trial.suggest_float("lr", 3e-4, 5e-3, log=True),
            "weight_decay": trial.suggest_float("weight_decay", 1e-5, 1e-3, log=True),
            "focal_gamma": trial.suggest_float("focal_gamma", 0.0, 3.0),
            "label_smoothing": trial.suggest_float("label_smoothing", 0.0, 0.15),
            "batch_size": trial.suggest_categorical("batch_size", [32, 64, 128]),
        }
        keras.utils.set_random_seed(trial.number)
        k = len(cfg["classes"])
        m = wafernet(x_tr.shape[1:], k, width=p["width"], dropout=p["dropout"])
        steps = int(np.ceil(len(x_tr) / p["batch_size"])) * a.epochs
        m.compile(keras.optimizers.AdamW(keras.optimizers.schedules.CosineDecay(p["lr"], steps), weight_decay=p["weight_decay"]),
                  focal_loss(p["focal_gamma"], None, p["label_smoothing"]), metrics=["accuracy"])
        seq = AugmentedSequence(x_tr, keras.utils.to_categorical(y_tr, k), p["batch_size"], cfg["augment_profile"], trial.number)
        best = 0.0
        for e in range(a.epochs):
            m.fit(seq, epochs=e + 1, initial_epoch=e, verbose=0)
            acc = float((m.predict(x_va, verbose=0).argmax(1) == y_va).mean())
            best = max(best, acc)
            trial.report(acc, e)
            if trial.should_prune():
                raise optuna.TrialPruned()
        return best

    study = optuna.create_study(direction="maximize", storage=a.storage, study_name="waferguard", load_if_exists=True,
                                pruner=optuna.pruners.MedianPruner(n_warmup_steps=4))
    study.optimize(objective, n_trials=a.trials)
    os.makedirs(cfg["output_dir"], exist_ok=True)
    out = os.path.join(cfg["output_dir"], "best_hparams.yaml")
    yaml.safe_dump({"best_val_accuracy": study.best_value, **study.best_params}, open(out, "w"))
    print(f"best val accuracy {study.best_value:.4f} with {study.best_params} -> {out}")


if __name__ == "__main__":
    main()
