# Model card: waferguard-ensemble 2026.09.27-0616

## Intended use
Classifying the spatial failure signature of a **wafer map** (die-level pass/fail map from wafer
sort / electrical test, or an equivalent defect-density map) into the 9 WM-811K classes, to
support yield engineers and flag lots for review. It is **not** a die-level SEM/optical defect
classifier, and it must not auto-scrap wafers without human confirmation of Critical results.

## Model
Weighted soft-voting ensemble, created 2026-09-27T06:16:28 UTC, trained on 1 CPU core (the run was resumed from per-epoch checkpoints several times; the last session took 6 min, total roughly 1 h of compute).

| Member | Type | Voting weight |
|---|---|---|
| wafernet_0 | wafernet | 0.55 |
| wafernet_1 | wafernet | 0.20 |
| gbm_features | portable_gbm | 0.25 |

Input: NHWC one-hot planes [background, pass, fail], float32, 64×64. Test-time augmentation: 8 dihedral views.

## Data
WM-811K wafer maps rendered as PNG (from the previous project's `dataset/`), 9 classes.
Leakage-safe split (seed 42): {"input_images": 9001, "exact_duplicates_removed": 1006, "near_duplicate_groups": 2, "sizes": {"train": 5589, "val": 1203, "test": 1203}}.
The original split had 17–38% test/train duplicates in six classes, which is why the split was rebuilt.

## Held-out test results (1203 wafers)
| Configuration | Test accuracy |
|---|---|
| wafernet 0 | 91.1% |
| wafernet 1 | 90.9% |
| gbm features | 88.4% |
| wafernet 0 + TTA | 91.4% |
| wafernet 1 + TTA | 91.9% |
| ensemble no tta | 92.1% |
| **Final ensemble + TTA (shipped)** | **92.4%** (macro-F1 92.4%) |

### Per class
| Class | Precision | Recall | F1 | Test wafers |
|---|---|---|---|---|
| No defect | 86.5% | 93.4% | 89.8% | 151 |
| Center cluster | 94.3% | 94.3% | 94.3% | 122 |
| Donut | 93.8% | 98.5% | 96.1% | 137 |
| Edge local | 88.3% | 83.5% | 85.8% | 127 |
| Edge ring | 97.3% | 96.7% | 97.0% | 150 |
| Local cluster | 87.5% | 82.0% | 84.7% | 128 |
| Random | 94.3% | 92.7% | 93.5% | 124 |
| Scratch | 92.6% | 90.0% | 91.3% | 140 |
| Near-full failure | 97.6% | 100.0% | 98.8% | 124 |

### Confusion matrix
| true \ predicted | none | Center | Donut | Edge-Loc | Edge-Ring | Local | Random | Scratch | Near-full |
|---|---|---|---|---|---|---|---|---|---|
| **none** | 141 | 1 | 1 | 2 | 2 | 2 | 0 | 2 | 0 |
| **Center** | 1 | 115 | 1 | 1 | 0 | 3 | 0 | 0 | 1 |
| **Donut** | 0 | 0 | 135 | 0 | 0 | 1 | 0 | 1 | 0 |
| **Edge-Loc** | 8 | 0 | 0 | 106 | 2 | 5 | 3 | 2 | 1 |
| **Edge-Ring** | 3 | 0 | 0 | 2 | 145 | 0 | 0 | 0 | 0 |
| **Local** | 2 | 4 | 4 | 6 | 0 | 105 | 3 | 4 | 0 |
| **Random** | 0 | 2 | 3 | 1 | 0 | 1 | 115 | 1 | 1 |
| **Scratch** | 8 | 0 | 0 | 2 | 0 | 3 | 1 | 126 | 0 |
| **Near-full** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 124 |

## Target
**Not reached on this data.** The shipped ensemble scores 92.4% on the leakage-safe held-out test split, below the 95.0% target. It was trained on one CPU core from ~5.6k training wafers. The weakest classes are Local cluster, Edge local, Scratch; their confusions are the classic ambiguous WM-811K pairs (Local vs Edge-Loc vs Scratch). Published >95% WM-811K figures usually use far more data, and often splits with duplicates.

## Limitations
- Trained on a 9k-map subset. Rare real classes (Near-full, Donut) are small in WM-811K itself.
- Camera photographs of wafers are handled by a heuristic conversion and are **outside the
  training distribution**. Results are flagged `input_domain: optical` and must be validated.
- Probabilities are not calibrated beyond label smoothing. Use `needs_review` (confidence below
  the configured threshold) to route uncertain wafers to engineers.
- Severity grades are policy rules (pattern + fail-die ratio), not learned. Tune them per fab.

## Maintenance
Retrain with `python -m waferguard.training.train`, compare with `scripts/evaluate.py`, deploy
through shadow A/B testing, and regenerate this card with `scripts/make_model_card.py`.
