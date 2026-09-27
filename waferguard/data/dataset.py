"""Folder-per-class dataset indexing with split detection and an .npz cache.

Layout expected (the format of the original wafer-cnn-app ``dataset/`` folder):

    dataset_root/
        <class_a>/ *.png|*.tif|*.bmp|*.jpg|*.npy
        <class_b>/ ...

If filenames start with ``train_``, ``validation_``/``val_`` or ``test_`` those
splits are respected (WM-811K export convention). Otherwise a stratified
70/15/15 split is generated with a fixed seed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

import numpy as np

from .io import is_supported, load_image_path
from .preprocess import MODEL_INPUT_SIZE, preprocess

_SPLIT_RE = re.compile(r"(?:^|_)(train|validation|val|test)_", re.IGNORECASE)


@dataclass
class DatasetIndex:
    classes: list[str]
    paths: list[str] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    splits: list[str] = field(default_factory=list)

    def subset(self, split: str) -> tuple[list[str], np.ndarray]:
        idx = [i for i, s in enumerate(self.splits) if s == split]
        return [self.paths[i] for i in idx], np.array([self.labels[i] for i in idx], dtype=np.int64)


def detect_split(filename: str) -> str | None:
    m = _SPLIT_RE.search(os.path.basename(filename))
    if not m:
        return None
    s = m.group(1).lower()
    return "val" if s in ("validation", "val") else s


def scan(root: str, classes: list[str] | None = None, seed: int = 42,
         val_frac: float = 0.15, test_frac: float = 0.15) -> DatasetIndex:
    if not os.path.isdir(root):
        raise FileNotFoundError(f"dataset root not found: {root}")
    found = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    classes = classes or found
    missing = [c for c in classes if c not in found]
    if missing:
        raise ValueError(f"class folders missing from {root}: {missing}")
    idx = DatasetIndex(classes=list(classes))
    rng = np.random.default_rng(seed)
    for ci, c in enumerate(classes):
        files = sorted(f for f in os.listdir(os.path.join(root, c)) if is_supported(f))
        splits = [detect_split(f) for f in files]
        if any(s is None for s in splits):
            order = rng.permutation(len(files))
            n_test = int(round(len(files) * test_frac))
            n_val = int(round(len(files) * val_frac))
            splits = [None] * len(files)
            for rank, j in enumerate(order):
                splits[j] = "test" if rank < n_test else ("val" if rank < n_test + n_val else "train")
        for f, s in zip(files, splits):
            idx.paths.append(os.path.join(root, c, f))
            idx.labels.append(ci)
            idx.splits.append(s)
    if not idx.paths:
        raise ValueError(f"no supported images under {root}")
    return idx


def _cache_key(paths: list[str], size: int) -> str:
    h = hashlib.sha1()
    h.update(str(size).encode())
    for p in paths:
        st = os.stat(p)
        h.update(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def load_arrays(paths: list[str], size: int = MODEL_INPUT_SIZE, cache_dir: str | None = None,
                progress: bool = False) -> np.ndarray:
    """Preprocess many files into an (N, size, size, 3) float32 array, cached on disk."""
    cache_file = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"x_{_cache_key(paths, size)}.npy")
        if os.path.exists(cache_file):
            return np.load(cache_file)
    out = np.zeros((len(paths), size, size, 3), dtype=np.float32)
    for i, p in enumerate(paths):
        out[i], _, _ = preprocess(load_image_path(p), size)
        if progress and i % 500 == 0:
            print(f"  preprocessed {i}/{len(paths)}", flush=True)
    if cache_file:
        np.save(cache_file, out)
    return out


def _group_key(x: np.ndarray) -> bytes:
    """Coarse 16x16 fail-pattern signature: exact and near-duplicate wafers share a key."""
    import cv2
    f = cv2.resize(x[..., 2], (16, 16), interpolation=cv2.INTER_AREA) > 0.3
    if f.sum() < 4:  # (almost) clean wafer: no pattern to be near-duplicate of, key on exact content
        return hashlib.md5((x > 0.5).tobytes()).digest()
    return np.packbits(f).tobytes()


def leakage_safe_split(x: np.ndarray, y: np.ndarray, seed: int = 42,
                       val_frac: float = 0.15, test_frac: float = 0.15) -> dict:
    """Drop exact duplicates and assign near-duplicate groups to a single split.

    The WM-811K export shipped with the original project had 17-38% of test
    images per class identical to training images, which inflates accuracy.
    Returns {"train": idx, "val": idx, "test": idx, "report": {...}}.
    """
    rng = np.random.default_rng(seed)
    exact: dict[bytes, int] = {}
    keep = []
    for i in range(len(x)):
        h = hashlib.md5((x[i] > 0.5).tobytes()).digest()
        if h in exact:
            continue
        exact[h] = i
        keep.append(i)
    groups: dict[tuple, list[int]] = {}
    for i in keep:
        groups.setdefault((int(y[i]), _group_key(x[i])), []).append(i)
    splits = {"train": [], "val": [], "test": []}
    for c in np.unique(y):
        cg = [g for (cls, _), g in groups.items() if cls == c]
        order = rng.permutation(len(cg))
        n = sum(len(g) for g in cg)
        taken = {"test": 0, "val": 0}
        for j in order:
            g = cg[j]
            if taken["test"] < test_frac * n:
                dest = "test"
            elif taken["val"] < val_frac * n:
                dest = "val"
            else:
                dest = "train"
            if dest in taken:
                taken[dest] += len(g)
            splits[dest].extend(g)
    out = {k: np.array(sorted(v), dtype=np.int64) for k, v in splits.items()}
    out["report"] = {
        "input_images": int(len(x)),
        "exact_duplicates_removed": int(len(x) - len(keep)),
        "near_duplicate_groups": int(sum(1 for g in groups.values() if len(g) > 1)),
        "sizes": {k: int(len(v)) for k, v in splits.items()},
    }
    return out


def summary(index: DatasetIndex) -> dict:
    out: dict = {"classes": index.classes, "splits": {}}
    for s in ("train", "val", "test"):
        _, y = index.subset(s)
        out["splits"][s] = {index.classes[c]: int((y == c).sum()) for c in range(len(index.classes))}
    return out


def write_summary(index: DatasetIndex, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(summary(index), fh, indent=2)
