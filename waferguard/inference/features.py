"""Rotation-robust geometry features of a wafer map.

Used by (a) the gradient-boosting member of the ensemble, which is diverse
from the CNNs and cheap to run on any CPU, and (b) the severity grader.
Inspired by the density / radon / geometry features of Wu et al.,
"Wafer Map Failure Pattern Recognition and Similarity Ranking" (IEEE TSM 2015).
"""
from __future__ import annotations

import cv2
import numpy as np

N_RINGS = 6
N_SECTORS = 8

_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _polar(size: int) -> tuple[np.ndarray, np.ndarray]:
    if size not in _cache:
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
        c = (size - 1) / 2.0
        r = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / (size / 2.0)
        a = (np.arctan2(yy - c, xx - c) + np.pi) / (2 * np.pi)
        _cache[size] = (r, a)
    return _cache[size]


def feature_names() -> list[str]:
    names = ["fail_ratio", "wafer_area"]
    names += [f"ring_{i}" for i in range(N_RINGS)]
    names += ["ring_slope", "ring_outer_minus_inner", "ring_peak_pos"]
    names += ["sector_std", "sector_max_over_mean", "sector_min_over_mean", "sector_top2_share"]
    names += ["n_clusters", "largest_share", "largest_area", "largest_r", "largest_ecc",
              "largest_extent", "largest_solidity", "largest_len", "top3_share", "mean_cluster_area"]
    names += ["radon_std_max", "radon_std_min", "radon_peak_ratio", "edge_band_fail", "center_disc_fail",
              "fail_pass_boundary", "isolated_fail_share"]
    return names


def extract(x: np.ndarray) -> np.ndarray:
    """x: (S, S, 3) one-hot tensor -> 1-D float32 feature vector."""
    s = x.shape[0]
    r, a = _polar(s)
    wafer = x[..., 0] < 0.5
    fail = x[..., 2]
    fb = (fail > 0.5) & wafer
    area = max(wafer.sum(), 1)
    feats = [float(fail[wafer].sum() / area), float(area / (s * s))]

    rings = []
    for i in range(N_RINGS):
        m = wafer & (r >= i / N_RINGS) & (r < (i + 1) / N_RINGS)
        rings.append(float(fail[m].mean()) if m.any() else 0.0)
    rings_a = np.array(rings)
    feats += rings
    feats += [float(np.polyfit(np.arange(N_RINGS), rings_a, 1)[0]),
              float(rings_a[-2:].mean() - rings_a[:2].mean()),
              float(np.argmax(rings_a) / (N_RINGS - 1))]

    sectors = []
    for j in range(N_SECTORS):
        m = wafer & (a >= j / N_SECTORS) & (a < (j + 1) / N_SECTORS)
        sectors.append(float(fail[m].mean()) if m.any() else 0.0)
    sec = np.array(sectors)
    mean = sec.mean() + 1e-6
    top2 = np.sort(sec)[-2:].sum() / (sec.sum() + 1e-6)
    feats += [float(sec.std()), float(sec.max() / mean), float(sec.min() / mean), float(top2)]

    n, lab, stats, cents = cv2.connectedComponentsWithStats(fb.astype(np.uint8), 8)
    total_fail = max(int(fb.sum()), 1)
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        order = np.argsort(areas)[::-1]
        li = 1 + int(order[0])
        comp = (lab == li).astype(np.uint8)
        cy, cx = cents[li][1], cents[li][0]
        lr = float(np.hypot(cx - (s - 1) / 2, cy - (s - 1) / 2) / (s / 2))
        pts = np.column_stack(np.nonzero(comp)).astype(np.float32)
        if len(pts) >= 3:
            cov = np.cov(pts.T)
            ev = np.sort(np.linalg.eigvalsh(cov))[::-1]
            ecc = float(np.sqrt(max(0.0, 1 - ev[1] / (ev[0] + 1e-6))))
            length = float(4 * np.sqrt(max(ev[0], 0)) / s)
        else:
            ecc, length = 0.0, 0.0
        w, h = stats[li, cv2.CC_STAT_WIDTH], stats[li, cv2.CC_STAT_HEIGHT]
        extent = float(areas[order[0]] / max(w * h, 1))
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull_area = cv2.contourArea(cv2.convexHull(cnts[0])) if cnts else 0
        solidity = float(areas[order[0]] / max(hull_area, 1))
        feats += [float(n - 1), float(areas[order[0]] / total_fail), float(areas[order[0]] / area), lr, ecc,
                  extent, min(solidity, 1.0), length, float(areas[order[:3]].sum() / total_fail),
                  float(areas.mean() / area)]
        isolated = float((areas <= 2).sum() * 1.0 / total_fail)
    else:
        feats += [0.0] * 10
        isolated = 0.0

    stds = []
    peaks = []
    for ang in (0, 45, 90, 135):
        m = cv2.getRotationMatrix2D(((s - 1) / 2, (s - 1) / 2), ang, 1.0)
        rot = cv2.warpAffine(fail * wafer, m, (s, s))
        proj = rot.sum(0)
        stds.append(proj.std())
        peaks.append(proj.max() / (proj.mean() + 1e-6))
    feats += [float(max(stds) / s), float(min(stds) / s), float(max(peaks) / (min(peaks) + 1e-6))]
    feats += [float(fail[wafer & (r > 0.8)].mean()) if (wafer & (r > 0.8)).any() else 0.0,
              float(fail[wafer & (r < 0.3)].mean()) if (wafer & (r < 0.3)).any() else 0.0]
    edges = cv2.Canny((fb * 255).astype(np.uint8), 50, 150)
    feats += [float(edges.sum() / 255.0 / total_fail), isolated]
    return np.asarray(feats, dtype=np.float32)


def extract_batch(xs: np.ndarray) -> np.ndarray:
    return np.stack([extract(x) for x in xs])
