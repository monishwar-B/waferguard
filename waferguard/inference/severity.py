"""Severity grading: Critical / Major / Minor / None.

Grade = max(base severity of the predicted pattern, grade implied by the
fraction of failing dies). Predictions below ``review_confidence`` are marked
``needs_review`` so an engineer confirms them; predictions below
``reject_confidence`` are additionally reported as ``Uncertain`` instead of a
class. All thresholds are configurable per deployment (deploy/configs/*.yaml).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from waferguard.taxonomy import BASE_SEVERITY, SEVERITY_ORDER


@dataclass
class SeverityPolicy:
    critical_fail_ratio: float = 0.30
    major_fail_ratio: float = 0.10
    minor_fail_ratio: float = 0.02
    review_confidence: float = 0.80
    reject_confidence: float = 0.40
    base: dict = field(default_factory=lambda: dict(BASE_SEVERITY))

    @classmethod
    def from_dict(cls, d: dict | None) -> "SeverityPolicy":
        d = dict(d or {})
        base = {**BASE_SEVERITY, **d.pop("base", {})}
        return cls(**d, base=base)


def _max(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.index(a) >= SEVERITY_ORDER.index(b) else b


def grade(label: str, confidence: float, fail_ratio: float, policy: SeverityPolicy | None = None) -> dict:
    p = policy or SeverityPolicy()
    base = p.base.get(label, "Minor")
    if label == "none":
        by_ratio = "None"
        if fail_ratio >= p.major_fail_ratio:  # "clean" pattern but many fails -> still escalate
            by_ratio = "Major" if fail_ratio < p.critical_fail_ratio else "Critical"
    elif fail_ratio >= p.critical_fail_ratio:
        by_ratio = "Critical"
    elif fail_ratio >= p.major_fail_ratio:
        by_ratio = "Major"
    elif fail_ratio >= p.minor_fail_ratio:
        by_ratio = "Minor"
    else:
        by_ratio = "None"
    sev = _max(base, by_ratio)
    reasons = [f"pattern '{label}' baseline {base}"]
    if by_ratio != "None":
        reasons.append(f"fail-die ratio {fail_ratio:.1%} implies {by_ratio}")
    needs_review = confidence < p.review_confidence
    if needs_review:
        reasons.append(f"confidence {confidence:.1%} below review threshold {p.review_confidence:.0%}")
    return {
        "severity": sev,
        "needs_review": needs_review,
        "uncertain": confidence < p.reject_confidence,
        "reasons": reasons,
    }
