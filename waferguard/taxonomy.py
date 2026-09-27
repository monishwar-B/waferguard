"""Defect taxonomy.

The shipped model is trained on the WM-811K wafer-map taxonomy (9 spatial
signature classes). The class list is NOT hard-coded anywhere else: training
writes it into the model manifest, and the inference engine reads it from
there. Retraining on a die-level SEM/optical dataset with classes such as
scratch / particle / void / bridge / open / short only needs a
folder-per-class dataset (see docs/TRAINING.md).
"""
from __future__ import annotations

WM811K_CLASSES = [
    "none", "Center", "Donut", "Edge-Loc", "Edge-Ring",
    "Local", "Random", "Scratch", "Near-full",
]

DISPLAY_NAMES = {
    "none": "No defect",
    "Center": "Center cluster",
    "Donut": "Donut",
    "Edge-Loc": "Edge local",
    "Edge-Ring": "Edge ring",
    "Local": "Local cluster",
    "Random": "Random",
    "Scratch": "Scratch",
    "Near-full": "Near-full failure",
}

# Base severity of a pattern, before the fail-die ratio is taken into account.
# Rationale: patterns that indicate a systemic, whole-tool excursion (Near-full,
# Random, Edge-Ring from chuck/CMP issues) are worse than a single local cluster.
BASE_SEVERITY = {
    "none": "None",
    "Center": "Major",
    "Donut": "Major",
    "Edge-Loc": "Minor",
    "Edge-Ring": "Major",
    "Local": "Minor",
    "Random": "Major",
    "Scratch": "Major",
    "Near-full": "Critical",
    # Die-level taxonomy (used when a custom model is trained on these classes)
    "scratch": "Major",
    "particle": "Minor",
    "void": "Major",
    "pattern": "Major",
    "bridge": "Critical",
    "open": "Critical",
    "short": "Critical",
}

# Likely process root causes, shown to engineers next to each result.
PROBABLE_CAUSES = {
    "Center": ["Spin-coat / develop non-uniformity", "CMP center-fast removal", "Chuck temperature gradient"],
    "Donut": ["Resist / CMP mid-radius non-uniformity", "Deposition chamber flow pattern"],
    "Edge-Loc": ["Wafer handling contact at edge", "Clamp ring / edge bead issue"],
    "Edge-Ring": ["Edge-bead removal problem", "CMP edge roll-off", "Etch uniformity at edge"],
    "Local": ["Localized particle contamination", "Mask / reticle defect"],
    "Random": ["Airborne particle contamination", "Chemical bath contamination"],
    "Scratch": ["Mechanical handling scratch", "CMP slurry agglomerate / pad debris"],
    "Near-full": ["Gross process failure (wrong recipe, tool fault)", "Probe card / test setup issue"],
    "none": [],
}

SEVERITY_ORDER = ["None", "Minor", "Major", "Critical"]


def display_name(label: str) -> str:
    return DISPLAY_NAMES.get(label, label)
