"""Write docs/MODEL_CARD.md and the README results table from a trained model's manifest.

    python scripts/write_model_card.py --model-dir models/wafer-ensemble

Numbers come only from manifest.json (the held-out test evaluation written by training),
so documentation can never drift from the shipped weights.
"""
import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = 0.95


def pct(v):
    return f"{100 * v:.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.path.join(ROOT, "models", "wafer-ensemble"))
    a = ap.parse_args()
    m = json.load(open(os.path.join(a.model_dir, "manifest.json")))
    t = m["test_metrics"]
    classes = m["classes"]
    abl = t.get("ablation_accuracy", {})
    split = m["dataset"]["split"]

    rows = ["| Configuration | Test accuracy |", "|---|---|"]
    for k, v in abl.items():
        rows.append(f"| {k.replace('+tta', ' + TTA')} | {pct(v)} |")
    rows.append(f"| **Ensemble + TTA (shipped)** | **{pct(t['accuracy'])}** |")
    results = "\n".join(rows) + f"\n\nMacro-F1 **{pct(t['macro_f1'])}**, log-loss {t['nll']:.3f}, " \
        f"on {sum(v['support'] for v in t['per_class'].values())} held-out wafers.\n\n"
    results += "| Class | Precision | Recall | F1 | n |\n|---|---|---|---|---|\n" + "\n".join(
        f"| {c} | {pct(v['precision'])} | {pct(v['recall'])} | {pct(v['f1'])} | {v['support']} |"
        for c, v in t["per_class"].items())

    acc = t["accuracy"]
    if acc >= TARGET:
        target = f"The shipped ensemble reaches {pct(acc)} on the leakage-safe held-out test split, meeting the >95% target."
    else:
        weakest = sorted(t["per_class"].items(), key=lambda kv: kv[1]["f1"])[:3]
        target = (f"**Not met.** The shipped ensemble reaches {pct(acc)} on the leakage-safe held-out test split "
                  f"({pct(TARGET - acc)} short). It was trained on one CPU core, from scratch, on the 9k-image subset in "
                  f"about {m.get('training_seconds', 0) / 60:.0f} minutes. The weakest classes are "
                  + ", ".join(f"{c} (F1 {pct(v['f1'])})" for c, v in weakest)
                  + ". Figures above 95% for WM-811K are typically reported on the full ~172k labelled set, "
                  "often on splits that were not checked for duplicates.")

    cm = m["confusion_matrix"]
    cm_md = "| true \\ predicted | " + " | ".join(classes) + " |\n|" + "---|" * (len(classes) + 1) + "\n" + "\n".join(
        f"| **{c}** | " + " | ".join(str(x) for x in row) + " |" for c, row in zip(classes, cm))

    card = f"""# Model card: {m['name']} {m['version']}

## Intended use
Classify the spatial failure signature of a wafer map (probe/test bin map) into the
{len(classes)} WM-811K classes, localize failing-die clusters, and grade severity, to support
yield engineers and operators. It is a decision-support tool: low-confidence results are
flagged for engineer review, and all results are audited.

**Not intended for:** optical or SEM defect review of individual dies (different data domain;
retrain on such images first, see docs/TRAINING.md), or automatic scrapping decisions without
human confirmation.

## Data
- Source: the WM-811K subset shipped with the original project, {split.get('input_images', '?')} images, {len(classes)} classes.
- {split.get('exact_duplicates_removed', '?')} exact duplicate images removed. Near-duplicate groups are kept inside one split.
- Split (stratified, seed 42): train {split['sizes']['train']}, validation {split['sizes']['val']}, test {split['sizes']['test']}.
- The validation split is used for early stopping and ensemble weights. The test split is used once, for the numbers below.

## Model
- Input: 64×64 one-hot die map (off-wafer / pass / fail), produced by `waferguard.data.preprocess`.
- Members: {', '.join(f"{x['name']} (weight {x.get('weight', 0):.2f})" for x in m['members'])}.
- Test-time augmentation: {'8 dihedral views' if m.get('tta') else 'off'}.
- Training: focal loss, class-balanced alpha, label smoothing, AdamW + cosine LR, hard negative mining,
  dihedral / affine / elastic / die-flip augmentation.

## Results (held-out test split)
{results}

### Confusion matrix
{cm_md}

## Accuracy target (>95%)
{target}

## Limitations
- The training data is a small, class-balanced subset. The real-world class frequency (about 85% `none`
  in WM-811K) differs, so validate precision on your own line before relying on alarms.
- Near-full has only 149 original wafers in WM-811K. That class in this dataset is largely derived
  from those, so its test score may be optimistic even after duplicate removal.
- Continuous-tone camera images are handled by a heuristic conversion and are flagged in every result.
- Localization is rule-based (failing-die clustering), not a learned segmentation model.

## Versioning
The version string is in `manifest.json` and stored with every inspection (`model_version`).
Compare candidates with `scripts/evaluate.py` and roll them out through shadow A/B testing.
"""
    open(os.path.join(ROOT, "docs", "MODEL_CARD.md"), "w").write(card)

    readme_p = os.path.join(ROOT, "README.md")
    readme = open(readme_p).read()
    readme = readme.replace("{{RESULTS_TABLE}}", results).replace("{{TARGET_STATEMENT}}", target)
    open(readme_p, "w").write(readme)
    print(f"accuracy {pct(acc)}, macro-F1 {pct(t['macro_f1'])}; wrote docs/MODEL_CARD.md and README results")
    if re.search(r"\{\{[A-Z_]+\}\}", readme):
        print("warning: README placeholders already replaced earlier; edit manually if re-running")


if __name__ == "__main__":
    main()
