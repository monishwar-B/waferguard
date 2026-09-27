"""Portable gradient-boosting model format.

A scikit-learn HistGradientBoostingClassifier is exported to plain numeric arrays
(``.npz``: node features, thresholds, children, leaf values, per-class baseline)
and evaluated here with vectorised NumPy. Unlike a pickle, this file loads on any
NumPy / scikit-learn version, so upgrading libraries or building a fresh Docker
image can never break model loading.
"""
from __future__ import annotations

import numpy as np


def export_hgb(clf, path: str) -> None:
    """Write a fitted multiclass HistGradientBoostingClassifier to ``path`` (.npz)."""
    preds = clf._predictors  # [n_iter][n_trees_per_iter]
    k = len(preds[0])
    feat, thr, left, right, leaf, value, miss_left, roots, depth = [], [], [], [], [], [], [], [], 0
    offset = 0
    for it in preds:
        if len(it) != k:
            raise ValueError("inconsistent trees per iteration")
        for tree in it:
            n = tree.nodes
            if n["is_categorical"].any():
                raise ValueError("categorical splits are not supported by the portable format")
            roots.append(offset)
            feat.append(n["feature_idx"].astype(np.int32))
            thr.append(n["num_threshold"].astype(np.float64))
            left.append(np.where(n["is_leaf"], 0, n["left"] + offset).astype(np.int64))
            right.append(np.where(n["is_leaf"], 0, n["right"] + offset).astype(np.int64))
            leaf.append(n["is_leaf"].astype(bool))
            value.append(n["value"].astype(np.float64))
            miss_left.append(n["missing_go_to_left"].astype(bool))
            depth = max(depth, int(n["depth"].max()))
            offset += len(n)
    baseline = np.asarray(clf._baseline_prediction, np.float64).reshape(-1)
    np.savez_compressed(
        path, feature=np.concatenate(feat), threshold=np.concatenate(thr), left=np.concatenate(left),
        right=np.concatenate(right), is_leaf=np.concatenate(leaf), value=np.concatenate(value),
        missing_left=np.concatenate(miss_left), roots=np.asarray(roots, np.int64),
        baseline=baseline, n_classes=np.int64(k), max_depth=np.int64(depth),
        classes=np.asarray(clf.classes_),
    )


class PortableHGB:
    """Loads an exported model and reproduces ``predict_proba``."""

    def __init__(self, path: str):
        z = np.load(path, allow_pickle=False)
        self.feature, self.threshold = z["feature"], z["threshold"]
        self.left, self.right, self.is_leaf = z["left"], z["right"], z["is_leaf"]
        self.value, self.missing_left, self.roots = z["value"], z["missing_left"], z["roots"]
        self.baseline, self.k, self.max_depth = z["baseline"], int(z["n_classes"]), int(z["max_depth"])
        self.classes_ = z["classes"]

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float64)
        n = len(x)
        rows = np.arange(n)[:, None]
        cur = np.broadcast_to(self.roots, (n, len(self.roots))).copy()
        for _ in range(self.max_depth + 1):
            leaf = self.is_leaf[cur]
            if leaf.all():
                break
            xv = x[rows, self.feature[cur]]
            go_left = (xv <= self.threshold[cur]) | (np.isnan(xv) & self.missing_left[cur])
            cur = np.where(leaf, cur, np.where(go_left, self.left[cur], self.right[cur]))
        raw = self.baseline + self.value[cur].reshape(n, -1, self.k).sum(axis=1)
        raw -= raw.max(axis=1, keepdims=True)
        e = np.exp(raw)
        return e / e.sum(axis=1, keepdims=True)
