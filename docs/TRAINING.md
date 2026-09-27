# Training and retraining

## 1. Dataset format

One folder per class. Any supported format works, and formats can be mixed:

```
dataset/
  none/       *.png | *.jpg | *.bmp | *.tif | *.npy
  Center/
  Donut/
  ...
```

- Filenames starting with `train_`, `validation_`/`val_` or `test_` are recognised. Training
  nevertheless uses the **leakage-safe split** by default: exact duplicates are removed, and
  near-duplicate groups stay in one split (70/15/15, stratified, seed 42). Set
  `leakage_safe_split: false` only if your filename splits are known to be clean.
- WM-811K raw `waferMap` arrays (values 0/1/2) can be saved directly as `.npy`.
  To convert the original `LSWMD.pkl` with pandas:
  `for i, row in df.iterrows(): np.save(f"dataset/{row.failureType}/{i}.npy", row.waferMap)`.
- The class list comes from `classes:` in the config (or from the folder names). It is written
  into `manifest.json`, so the API, UI, exports and SPC all pick up new classes automatically.

### Die-level taxonomies (scratch / particle / void / pattern / bridge / open / short / none)

Put SEM or optical die crops in class folders, set
`classes: [none, scratch, particle, void, pattern, bridge, open, short]`
and `augment_profile: image` (brightness/contrast/Gaussian noise instead of die-flip noise).
Continuous-tone images are converted by the same preprocessing function. For photographic
data you will get better results from a transfer-learning backbone (below) at a larger
`input_size` (e.g. 224). Severity defaults for these class names already exist in
`waferguard/taxonomy.py`.

## 2. Train

```bash
pip install -r requirements-train.txt
python -m waferguard.training.train --config deploy/configs/train.yaml --data-root /path/to/dataset
# or: docker build -f deploy/docker/Dockerfile.train -t wg-train . && \
#     docker run --gpus all -v /path/to/dataset:/data/dataset -v $PWD/models:/app/models wg-train
```

The run writes to `output_dir` (default `models/wafer-ensemble/`):
- `manifest.json`: classes, members, voting weights, split report, test metrics, confusion matrix
- `*.onnx` + `*.keras`: CNN members
- `gbm_features.joblib`: gradient-boosting member
- `training_history.json`

Training is **resumable**. Per-epoch checkpoints (`*.epoch.weights.h5`, `*.state.json`) let a
killed run continue from the last completed epoch when started again with the same command.
Finished members are skipped.

### What the pipeline does

| Technique | Where / setting |
|---|---|
| Augmentation: 8 dihedral symmetries, affine jitter, elastic deformation, die-flip noise (maps) or brightness/contrast/Gaussian noise (images) | `training/augment.py`, `augment_profile` |
| Focal loss with class-balanced alpha + label smoothing | `focal_gamma`, `label_smoothing` |
| Hard negative mining: the hardest 20% of samples are up-weighted each epoch | `hard_negative_mining` |
| Cosine LR schedule, AdamW, early stopping on val accuracy | `lr`, `weight_decay`, `early_stopping_patience` |
| Semi-supervised pseudo-labelling (confident ensemble predictions on unlabelled data, reduced weight, fine-tune round) | `pseudo_label`, or `--unlabeled-dir` |
| Ensemble with weighted soft voting (weights fitted on validation) | `members`, `gbm` |
| Test-time augmentation (8 views) | `tta` |
| Experiment tracking / versioning | MLflow (auto-detected) + versioned `manifest.json` |

### GPU backbones (transfer learning from ImageNet)

Add members to the config:

```yaml
input_size: 64          # backbones resize internally to their native resolution
members:
  - {arch: wafernet, width: 32, seed: 1}
  - {arch: efficientnet_b4, seed: 3, dropout: 0.4}
  - {arch: convnext_tiny, seed: 4}
  - {arch: vit_small, seed: 5, patch: 8}
```

Available: `wafernet`, `efficientnet_b4`, `efficientnet_b7`, `efficientnetv2_s`, `convnext_tiny`,
`resnet152v2`, `vit_small`. (Keras ships no ResNeXt-101. ConvNeXt is its modern successor
and is offered instead.) EfficientNet-B7 at 600 px needs a large GPU; B4 or ConvNeXt-T
are the practical choices.

## 3. Hyperparameter search

```bash
python -m waferguard.training.tune --config deploy/configs/train.yaml --trials 40 --epochs 12 \
       --storage sqlite:///optuna.db
```

This searches width, dropout, LR, weight decay, focal gamma, label smoothing and batch size,
with median pruning. The best parameters go to `<output_dir>/best_hparams.yaml`.

## 4. Evaluate, compare, deploy

```bash
python scripts/evaluate.py --model-dir models/wafer-ensemble --data-root dataset --split test
python scripts/evaluate.py --model-dir models/candidate      --data-root dataset --split test
python scripts/evaluate.py --model-dir models/wafer-ensemble --data-root new_lot_labelled   # whole folder
```

To deploy a new model safely:
1. Copy it next to the current one (e.g. `models/wafer-ensemble-v2`).
2. Administration → Models: set the challenger folder, choose **Shadow**, and let it run on
   live wafers.
3. Compare agreement, and reviewed accuracy once engineers review disagreements.
4. **Promote**. Rollback is the same button in reverse.

## 5. Improving accuracy: what actually helps

1. **More real data.** This dataset is a 9k subset of WM-811K, which has ~172k labelled maps.
   Rare classes (Near-full: 149 real wafers, Donut: 555) limit performance most.
2. **Pseudo-labelling** of the ~640k unlabelled WM-811K maps (`--unlabeled-dir`).
3. **Engineer reviews.** Every corrected label in History is a new training sample. Export them
   with `GET /api/v1/export/inspections.csv?...` and the images ZIP.
4. **Larger or more members on a GPU**, then Optuna.
