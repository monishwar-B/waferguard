"""Augmentation and the training batch generator.

Two profiles:
  * ``wafer_map`` (default): dihedral rotations/flips, elastic deformation, small
    scale/shift jitter and die-level flip noise (the discrete analogue of Gaussian
    noise for pass/fail maps).
  * ``image``: for continuous-tone datasets (SEM / optical die images): the above
    geometry plus brightness/contrast jitter and additive Gaussian noise.

Hard negative mining is implemented through per-sample weights that the trainer
updates after every epoch (see train.py).
"""
from __future__ import annotations

import cv2
import keras
import numpy as np

from waferguard.data.preprocess import dihedral


def focal_loss(gamma: float = 2.0, alpha=None, label_smoothing: float = 0.0):
    """Categorical focal loss (Lin et al., 2017) with optional per-class alpha."""
    alpha_t = None if alpha is None else keras.ops.convert_to_tensor(np.asarray(alpha, "float32"))

    def loss(y_true, y_pred):
        n = keras.ops.shape(y_pred)[-1]
        y_true = keras.ops.cast(y_true, "float32")
        if label_smoothing:
            y_true = y_true * (1 - label_smoothing) + label_smoothing / keras.ops.cast(n, "float32")
        p = keras.ops.clip(y_pred, 1e-7, 1 - 1e-7)
        ce = -y_true * keras.ops.log(p)
        w = keras.ops.power(1 - p, gamma)
        if alpha_t is not None:
            w = w * alpha_t
        return keras.ops.sum(w * ce, axis=-1)

    loss.__name__ = "focal_loss"
    return loss


def elastic(x: np.ndarray, rng: np.random.Generator, alpha: float = 3.0, sigma: float = 6.0) -> np.ndarray:
    s = x.shape[0]
    dx = cv2.GaussianBlur(rng.uniform(-1, 1, (s, s)).astype(np.float32), (0, 0), sigma) * alpha
    dy = cv2.GaussianBlur(rng.uniform(-1, 1, (s, s)).astype(np.float32), (0, 0), sigma) * alpha
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    return cv2.remap(x, xx + dx, yy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def affine_jitter(x: np.ndarray, rng: np.random.Generator, scale=0.06, shift=0.04) -> np.ndarray:
    s = x.shape[0]
    m = cv2.getRotationMatrix2D((s / 2, s / 2), rng.uniform(-10, 10), 1 + rng.uniform(-scale, scale))
    m[:, 2] += rng.uniform(-shift, shift, 2) * s
    bg = np.zeros(x.shape[-1], np.float32)
    bg[0] = 1.0  # outside = background plane
    out = cv2.warpAffine(x, m, (s, s), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=tuple(float(v) for v in bg))
    return out


def die_flip_noise(x: np.ndarray, rng: np.random.Generator, rate: float = 0.01) -> np.ndarray:
    wafer = x[..., 0] < 0.5
    flip = (rng.random(x.shape[:2]) < rate) & wafer
    y = x.copy()
    y[flip, 1], y[flip, 2] = x[flip, 2], x[flip, 1]
    return y


def augment_one(x: np.ndarray, rng: np.random.Generator, profile: str = "wafer_map") -> np.ndarray:
    y = dihedral(x, int(rng.integers(8)))
    if rng.random() < 0.5:
        y = affine_jitter(y, rng)
    if rng.random() < 0.3:
        y = elastic(y, rng)
    if profile == "wafer_map":
        if rng.random() < 0.5:
            y = die_flip_noise(y, rng, rate=float(rng.uniform(0.0, 0.02)))
        y = np.clip(y, 0, 1)
        y /= np.maximum(y.sum(-1, keepdims=True), 1e-6)  # keep a valid one-hot mixture
    else:
        y = y * rng.uniform(0.8, 1.2) + rng.uniform(-0.1, 0.1)                      # contrast / brightness
        y = y + rng.normal(0, rng.uniform(0, 0.05), y.shape).astype(np.float32)  # gaussian noise
        y = np.clip(y, 0, 1)
    return y.astype(np.float32)


class AugmentedSequence(keras.utils.PyDataset):
    """Shuffled, augmented batches with optional per-sample weights (hard negative mining)."""

    def __init__(self, x, y_onehot, batch_size=64, profile="wafer_map", seed=0, weights=None, **kw):
        super().__init__(**kw)
        self.x, self.y = x, y_onehot
        self.bs, self.profile = batch_size, profile
        self.rng = np.random.default_rng(seed)
        self.weights = np.ones(len(x), np.float32) if weights is None else weights
        self.order = self.rng.permutation(len(x))

    def __len__(self):
        return int(np.ceil(len(self.x) / self.bs))

    def __getitem__(self, i):
        idx = self.order[i * self.bs:(i + 1) * self.bs]
        xb = np.stack([augment_one(self.x[j], self.rng, self.profile) for j in idx])
        return xb, self.y[idx], self.weights[idx]

    def on_epoch_end(self):
        self.order = self.rng.permutation(len(self.x))


def hard_negative_weights(y_true: np.ndarray, probs: np.ndarray, strength: float = 1.0,
                          top_frac: float = 0.2) -> np.ndarray:
    """Up-weight the hardest ``top_frac`` samples (highest loss) by up to 1+strength."""
    p_true = probs[np.arange(len(y_true)), y_true]
    loss = -np.log(np.clip(p_true, 1e-7, 1))
    thr = np.quantile(loss, 1 - top_frac)
    w = np.ones_like(loss, dtype=np.float32)
    hard = loss >= thr
    if hard.any() and loss.max() > thr:
        w[hard] = 1 + strength * (loss[hard] - thr) / (loss.max() - thr + 1e-6)
    return w / w.mean()
