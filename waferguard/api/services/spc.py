"""Statistical process control.

* p-chart of the defective-wafer proportion per subgroup (lot, or time bucket)
  with variable-n 3-sigma limits.
* Individuals / moving-range (I-MR) chart of the per-wafer fail-die ratio.
* Process capability of the fail-die ratio against an upper spec limit (USL):
    Cpk = (USL - mean) / (3 * sigma_within),  sigma_within = MR-bar / d2 (d2 = 1.128)
    Ppk = (USL - mean) / (3 * sigma_overall), sigma_overall = sample std
  One-sided (upper) because a lower fail ratio is always better.
* Western Electric rules 1-4 on any chart series.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

D2 = 1.128  # control-chart constant for moving ranges of size 2


def p_chart(subgroups: list[tuple[str, int, int]]) -> dict:
    """subgroups: [(key, defective, n)] in time order."""
    subgroups = [g for g in subgroups if g[2] > 0]
    if not subgroups:
        return {"center": None, "points": []}
    total_def = sum(d for _, d, _ in subgroups)
    total_n = sum(n for _, _, n in subgroups)
    pbar = total_def / total_n
    pts = []
    for key, d, n in subgroups:
        sigma = math.sqrt(pbar * (1 - pbar) / n)
        ucl, lcl = min(1.0, pbar + 3 * sigma), max(0.0, pbar - 3 * sigma)
        p = d / n
        pts.append({"key": key, "p": p, "n": n, "defective": d, "ucl": ucl, "lcl": lcl,
                    "out_of_control": p > ucl or p < lcl, "sigma": sigma})
    violations = western_electric([p["p"] for p in pts], pbar, [p["sigma"] for p in pts])
    for v in violations:
        pts[v["index"]].setdefault("rules", []).append(v["rule"])
    return {"center": pbar, "points": pts, "violations": violations}


def imr_chart(values: list[float]) -> dict:
    x = np.asarray(values, float)
    if len(x) < 2:
        return {"center": float(x.mean()) if len(x) else None, "points": [], "mr_bar": None}
    mr = np.abs(np.diff(x))
    mr_bar = float(mr.mean())
    sigma = mr_bar / D2
    c = float(x.mean())
    return {"center": c, "ucl": c + 3 * sigma, "lcl": max(0.0, c - 3 * sigma), "mr_bar": mr_bar,
            "mr_ucl": 3.267 * mr_bar, "sigma_within": sigma,
            "points": [{"i": i, "x": float(v), "mr": float(mr[i - 1]) if i else None} for i, v in enumerate(x)],
            "violations": western_electric(list(x), c, [sigma] * len(x))}


def capability(values: list[float], usl: float, lsl: float | None = None) -> dict:
    x = np.asarray(values, float)
    if len(x) < 2:
        return {"n": int(len(x)), "cpk": None, "ppk": None}
    mean = float(x.mean())
    s_overall = float(x.std(ddof=1))
    s_within = float(np.abs(np.diff(x)).mean() / D2)

    def idx(sigma):
        if sigma <= 0:
            return None
        cands = [(usl - mean) / (3 * sigma)]
        if lsl is not None:
            cands.append((mean - lsl) / (3 * sigma))
        return float(min(cands))

    cpk, ppk = idx(s_within), idx(s_overall)
    frac_over = float((x > usl).mean())
    return {"n": int(len(x)), "mean": mean, "sigma_within": s_within, "sigma_overall": s_overall,
            "usl": usl, "lsl": lsl, "cpk": cpk, "ppk": ppk, "fraction_above_usl": frac_over,
            "rating": _rating(cpk)}


def _rating(cpk):
    if cpk is None:
        return "insufficient data"
    if cpk >= 1.67:
        return "excellent"
    if cpk >= 1.33:
        return "capable"
    if cpk >= 1.0:
        return "marginal"
    return "not capable"


def western_electric(values: list[float], center: float, sigmas: list[float]) -> list[dict]:
    """Rules: 1) one point beyond 3s; 2) 2 of 3 beyond 2s same side; 3) 4 of 5 beyond 1s same side;
    4) 8 consecutive on the same side of the centre line."""
    out = []
    z = [(v - center) / s if s > 0 else 0.0 for v, s in zip(values, sigmas)]
    for i, zi in enumerate(z):
        if abs(zi) > 3:
            out.append({"index": i, "rule": 1, "description": "point beyond 3 sigma"})
        for rule, win, need, lim in ((2, 3, 2, 2), (3, 5, 4, 1)):
            if i >= win - 1:
                w = z[i - win + 1: i + 1]
                for sign in (1, -1):
                    if sum(1 for v in w if sign * v > lim) >= need and sign * zi > lim:
                        out.append({"index": i, "rule": rule,
                                    "description": f"{need} of {win} beyond {lim} sigma on one side"})
        if i >= 7:
            w = z[i - 7: i + 1]
            if all(v > 0 for v in w) or all(v < 0 for v in w):
                out.append({"index": i, "rule": 4, "description": "8 consecutive points on one side of centre"})
    return out


def pareto(labels: list[str]) -> list[dict]:
    c = Counter(l for l in labels if l != "none")
    total = sum(c.values()) or 1
    cum, out = 0, []
    for label, n in c.most_common():
        cum += n
        out.append({"label": label, "count": n, "share": n / total, "cumulative": cum / total})
    return out


def trend(rows: list[tuple[str, str]]) -> list[dict]:
    """rows: [(bucket, label)] -> per-bucket counts by label, bucket order preserved."""
    buckets: dict[str, Counter] = defaultdict(Counter)
    order = []
    for b, label in rows:
        if b not in buckets:
            order.append(b)
        buckets[b][label] += 1
    return [{"bucket": b, "total": sum(buckets[b].values()), "counts": dict(buckets[b]),
             "defect_rate": 1 - buckets[b].get("none", 0) / max(sum(buckets[b].values()), 1)} for b in order]
