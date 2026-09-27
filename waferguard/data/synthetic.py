"""Procedural wafer maps for tests, demos and the ``synthetic:`` camera source.

Not used for training the shipped model (real WM-811K data is), but handy to
exercise the whole pipeline without data or hardware.
"""
from __future__ import annotations

import numpy as np

from waferguard.inference.localization import LEVEL_COLORS

WM_COLORS = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], np.uint8)  # WM-811K export palette


def _grid(n):
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    c = (n - 1) / 2
    r = np.hypot(xx - c, yy - c) / (n / 2)
    a = np.arctan2(yy - c, xx - c)
    return xx, yy, r, a


def make(label: str, n: int = 48, rng: np.random.Generator | None = None) -> np.ndarray:
    """Return an (n, n) level map: 0 off-wafer, 1 pass, 2 fail."""
    rng = rng or np.random.default_rng()
    xx, yy, r, a = _grid(n)
    wafer = r <= 0.97
    fail = rng.random((n, n)) < 0.015  # background random fails
    if label == "Center":
        fail |= r < rng.uniform(0.2, 0.35)
    elif label == "Donut":
        r0 = rng.uniform(0.4, 0.55)
        fail |= np.abs(r - r0) < 0.1
    elif label == "Edge-Ring":
        fail |= r > rng.uniform(0.82, 0.88)
    elif label == "Edge-Loc":
        a0 = rng.uniform(-np.pi, np.pi)
        fail |= (r > 0.75) & (np.abs(np.angle(np.exp(1j * (a - a0)))) < 0.5)
    elif label == "Local":
        cx, cy = rng.uniform(0.3, 0.7, 2) * n
        fail |= np.hypot(xx - cx, yy - cy) < n * rng.uniform(0.08, 0.13)
    elif label == "Random":
        fail |= rng.random((n, n)) < rng.uniform(0.15, 0.3)
    elif label == "Scratch":
        t = rng.uniform(0, np.pi)
        x0, y0 = rng.uniform(0.35, 0.65, 2) * n
        d = np.abs((xx - x0) * np.sin(t) - (yy - y0) * np.cos(t))
        along = (xx - x0) * np.cos(t) + (yy - y0) * np.sin(t)
        fail |= (d < 1.0) & (np.abs(along) < n * 0.35)
    elif label == "Near-full":
        fail |= rng.random((n, n)) < 0.85
    levels = np.where(wafer, np.where(fail, 2, 1), 0).astype(np.uint8)
    return levels


def to_rgb(levels: np.ndarray, palette: str = "wm811k", scale: int = 4) -> np.ndarray:
    pal = WM_COLORS if palette == "wm811k" else LEVEL_COLORS
    img = pal[levels]
    return np.repeat(np.repeat(img, scale, 0), scale, 1)


def dataset(labels: list[str], per_class: int, n: int = 48, seed: int = 0):
    rng = np.random.default_rng(seed)
    xs, ys = [], []
    for i, lab in enumerate(labels):
        for _ in range(per_class):
            xs.append(make(lab, n, rng))
            ys.append(i)
    return xs, np.array(ys)
