"""Canonical preprocessing shared by training and inference.

Every input is first converted to a *die-level map* with three levels:
    0 = off-wafer / background, 1 = passing die / clean area, 2 = failing die / defect pixel
and then to a (S, S, 3) float32 one-hot tensor. Training and serving call
exactly the same functions, so there is no train/serve skew.

Three input domains are recognised and reported back to the caller:
  * ``wafer_map_rgb``   colour-coded wafer maps (the WM-811K renderings used for training)
  * ``wafer_map_levels`` arrays / grayscale images with <=3 discrete levels (raw WM-811K ``waferMap``)
  * ``optical``         continuous-tone camera frames. These are converted with a
                        heuristic (wafer segmentation + local anomaly detection). The
                        shipped model was NOT trained on camera images, so results for
                        this domain are flagged and should be validated on your tool.
"""
from __future__ import annotations

import cv2
import numpy as np

MODEL_INPUT_SIZE = 64


def _is_color_coded(rgb: np.ndarray) -> bool:
    """True if nearly every pixel is a pure-ish red, green or blue."""
    small = rgb[:: max(1, rgb.shape[0] // 96), :: max(1, rgb.shape[1] // 96)].reshape(-1, 3).astype(np.int16)
    mx = small.max(1)
    second = np.sort(small, axis=1)[:, 1]
    dominant = (mx > 100) & (mx - second > 80)
    return bool(dominant.mean() > 0.9)


def _decode_color_coded(rgb: np.ndarray) -> np.ndarray:
    # R -> background (0), G -> pass (1), B -> fail (2)
    return np.argmax(rgb[..., :3], axis=-1).astype(np.uint8)


def _to_gray_float(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(-1)
    lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.5)
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def _optical_to_levels(arr: np.ndarray, k: float = 4.0) -> np.ndarray:
    """Heuristic: segment the wafer disc, then mark local intensity anomalies as defects."""
    g = _to_gray_float(arr)
    g8 = (g * 255).astype(np.uint8)
    _, fg = cv2.threshold(g8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # choose the polarity that makes the wafer the larger central object
    h, w = fg.shape
    centre = fg[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    if centre.mean() < 127:
        fg = 255 - fg
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n > 1:
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        wafer = (lab == biggest).astype(np.uint8)
    else:
        wafer = np.ones_like(fg, dtype=np.uint8)
    contours, _ = cv2.findContours(wafer, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    wafer = np.zeros_like(wafer)
    cv2.drawContours(wafer, contours, -1, 1, thickness=-1)
    # background = large median filter computed with the off-wafer area filled by the wafer median,
    # so the wafer edge itself is not reported as a defect
    ksize = max(3, (min(h, w) // 6) | 1)
    fill = g8.copy()
    fill[wafer == 0] = int(np.median(g8[wafer > 0])) if wafer.any() else 0
    background = cv2.medianBlur(fill, min(ksize, 99))
    resid = fill.astype(np.float32) - background.astype(np.float32)
    inside = resid[wafer > 0]
    if inside.size == 0:
        return wafer.astype(np.uint8)
    med = np.median(inside)
    mad = np.median(np.abs(inside - med)) + 1e-3
    defect = (np.abs(resid - med) > k * 1.4826 * mad) & (wafer > 0)
    defect = cv2.morphologyEx(defect.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    levels = wafer.astype(np.uint8)
    levels[defect > 0] = 2
    return levels


def to_levels(arr: np.ndarray) -> tuple[np.ndarray, str]:
    """Convert any decoded image to a 3-level die map. Returns (levels, domain)."""
    if arr is None or arr.size == 0:
        raise ValueError("empty image")
    if arr.ndim == 3 and arr.shape[-1] >= 3:
        rgb = arr[..., :3]
        if rgb.dtype == np.uint8 and _is_color_coded(rgb):
            return _decode_color_coded(rgb), "wafer_map_rgb"
        if np.allclose(rgb[..., 0], rgb[..., 1]) and np.allclose(rgb[..., 1], rgb[..., 2]):
            arr = rgb[..., 0]
    if arr.ndim == 3:
        arr = arr[..., :3].mean(-1)
    uniq = np.unique(arr)
    if uniq.size <= 3 and np.all(np.equal(np.mod(uniq, 1), 0)):
        if set(uniq.tolist()) <= {0, 1, 2}:
            return arr.astype(np.uint8), "wafer_map_levels"
        mapping = {v: i for i, v in enumerate(sorted(uniq.tolist()))}
        out = np.vectorize(mapping.get)(arr).astype(np.uint8)
        return out, "wafer_map_levels"
    return _optical_to_levels(arr), "optical"


def crop_to_wafer(levels: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(levels > 0)
    if ys.size == 0:
        return levels
    return levels[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def levels_to_tensor(levels: np.ndarray, size: int = MODEL_INPUT_SIZE) -> np.ndarray:
    """(H, W) levels -> (size, size, 3) float32 one-hot, area-resampled."""
    lv = crop_to_wafer(levels)
    planes = np.stack([(lv == i).astype(np.float32) for i in range(3)], axis=-1)
    return cv2.resize(planes, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)


def preprocess(arr: np.ndarray, size: int = MODEL_INPUT_SIZE) -> tuple[np.ndarray, np.ndarray, str]:
    """Full pipeline. Returns (tensor, levels, domain)."""
    levels, domain = to_levels(arr)
    return levels_to_tensor(levels, size), levels, domain


def dihedral(x: np.ndarray, k: int) -> np.ndarray:
    """The 8 symmetries of the square (used for TTA and augmentation). x is (..., H, W, C)."""
    y = np.rot90(x, k % 4, axes=(-3, -2))
    if k >= 4:
        y = np.flip(y, axis=-2)
    return np.ascontiguousarray(y)
