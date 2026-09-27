# WaferGuard: wafer defect inspection system

WaferGuard classifies wafer-map defect patterns, localizes the failing-die clusters,
grades severity, tracks every wafer with lot / wafer / equipment / operator IDs, runs
statistical process control, and raises alerts when the line drifts. It runs as a web
application (Docker, Kubernetes) and as a standalone desktop application, from the
same code base.

It is a rebuild of the earlier `wafer-cnn-app` Flask project. The existing WM-811K
dataset and taxonomy are kept. The architecture, model pipeline and evaluation
protocol are new.

---

## Quick start

**Deploying for real? Follow `docs/DEPLOY_QUICKSTART.md`.** It has step-by-step instructions for a Windows PC or local network (Docker Desktop) and for the internet (cloud VM with HTTPS).

| Scenario | Command | Then open |
|---|---|---|
| Full stack (Postgres, Redis, workers, Prometheus, Grafana) | `docker compose up -d` | http://localhost:8000 |
| From source, no Docker (SQLite, in-process queue) | `./scripts/run_local.sh` | http://localhost:8000 |
| Desktop window | `pip install -r requirements-desktop.txt && python -m waferguard.desktop.launcher` | opens itself |
| Offline command line | `waferguard predict sample_data/wm811k --out results.csv` | CSV |

Default login is **admin / admin123**. It is created on first start, so change it immediately
(Administration → Users), or set `WG_BOOTSTRAP_ADMIN_PASSWORD` before the first start.
API docs are at `/docs` (Swagger) and `/redoc`. Grafana runs on :3000 and Prometheus on :9090.

To try the live-camera path without hardware, open Line monitor, set Source to
`synthetic:sample_data/wm811k` and choose Start camera.

---

## Model and measured accuracy

**Read this before quoting numbers.**

**The old evaluation was inflated.** The dataset shipped with the previous project
(9 classes × ~1000 WM-811K maps) had **1,006 exact duplicate images**. Between **17% and 38% of
the test images in six classes were pixel-identical to training images**. Every accuracy figure
from that split is inflated, including the old app's 79.6%. WaferGuard trains and evaluates on
a *leakage-safe split* instead: exact duplicates are removed, and near-duplicates (same coarse
fail pattern) are kept inside one split. That leaves 7,995 unique wafers:
5,589 train / 1,203 validation / 1,203 test.

**Shipped ensemble** (`models/wafer-ensemble/`, see `manifest.json` for exact numbers):

| Member | Type | Notes |
|---|---|---|
| `wafernet_0` | Residual CNN with squeeze-excitation, width 24 | trained from scratch, ONNX |
| `wafernet_1` | Same architecture, width 16, different seed | ONNX |
| `gbm_features` | Gradient boosting on 32 rotation-robust geometry features | scikit-learn |

The members are combined by weighted soft voting, with weights fitted on the validation split,
plus 8-way dihedral test-time augmentation for the CNNs.

**Held-out test results** (1,203 wafers never used for training, weight fitting or model selection):

| Configuration | Test accuracy |
|---|---|
| wafernet 0 | 91.1% |
| wafernet 1 | 90.9% |
| gbm features | 88.2% |
| wafernet 0 + TTA | 91.4% |
| wafernet 1 + TTA | 91.9% |
| ensemble no tta | 92.4% |
| **Final ensemble + TTA (shipped)** | **92.2%** (macro-F1 92.1%) |

TTA helped each CNN on its own (+0.3 and +1.0 points), but on this test set the ensemble
*without* TTA scored slightly higher (92.4% vs 92.2%). That is 2 wafers out of 1,203,
within noise. TTA stays on because it was chosen on the validation split, and the test split
is not used for decisions. On latency-bound edge devices, `models.tta: false` costs nothing measurable.

`models/wafer-ensemble-int8/` is a post-training INT8 version. It scores **91.6%** on the same
test split and runs **2.2× faster (7.9 vs 17.3 ms/wafer on one CPU core, TTA on)** with 3.4×
smaller CNN files. Use it on CPU / OpenVINO edge boxes, or deploy it as a shadow challenger first.
The ONNX Runtime serving path reproduces the training-time test accuracy exactly
(`scripts/evaluate.py`).

**About the >95% target.** **Not reached on this data.** The shipped ensemble scores 92.2% on the leakage-safe held-out test split, below the 95.0% target. It was trained on one CPU core from ~5.6k training wafers. The weakest classes are Local cluster, Edge local, Scratch; their confusions are the classic ambiguous WM-811K pairs (Local vs Edge-Loc vs Scratch). Published >95% WM-811K figures usually use far more data, and often splits with duplicates.

The pipeline contains everything needed to push further on a GPU:
- EfficientNet-B4/B7, ConvNeXt, ResNet152V2 and ViT backbones with ImageNet transfer learning
- Optuna hyperparameter search
- pseudo-labelling of the ~640k unlabelled WM-811K maps

The most promising lever is data. WM-811K has only 149 real Near-full and 555 Donut wafers,
so the full labelled set (~172k maps) plus unlabelled pseudo-labels matters more than a bigger
network. See `docs/TRAINING.md`.

**Taxonomy.** The model recognises the 9 WM-811K spatial signatures (Center, Donut, Edge-Loc,
Edge-Ring, Local, Random, Scratch, Near-full, none). Die-level defect types such as particle,
void, bridge, open and short are visible only in SEM or optical die images. They cannot be
learned from wafer maps. The class list is data-driven: train on a folder-per-class die-image
dataset and those classes appear everywhere in the system (see `docs/TRAINING.md`).

---

## What is in the box

| Area | Implementation |
|---|---|
| Input formats | PNG, JPEG, BMP, TIFF (8/16-bit, multi-page), NumPy `.npy`, headerless raw sensor dumps (`.raw/.bin` with width/height/dtype) |
| Real-time | Server-side cameras: USB/UVC, RTSP, GigE Vision via GStreamer/Aravis, GenICam/GenTL (USB3 Vision + GigE) via `harvesters`, synthetic replay. Adjustable 1–30 FPS, live over WebSocket. Browser-side kiosk camera capture. |
| Throughput | Batch jobs (multi-file / ZIP) on an in-process pool or a Redis queue with horizontally scaled workers |
| Model | Weighted ensemble, TTA, focal loss, hard-negative mining, augmentation (dihedral, elastic, affine, die-flip noise, brightness/contrast/Gaussian for image datasets), pseudo-labelling, Optuna, ONNX export, INT8 quantization |
| Localization | Failing-die clustering → bounding boxes, polygons, binary segmentation masks |
| Severity | Critical / Major / Minor / None from pattern + fail-die ratio. Review and reject confidence thresholds are configurable. |
| SPC | p-chart (variable n), I-MR chart, Cpk / Ppk, Western Electric rules 1–4, Pareto, trend, per-equipment rates |
| Alerts | Defect-rate spike, consecutive defects and critical wafer rules. Email (SMTP), Microsoft Teams, SMS (Twilio), generic webhook. Cooldowns. |
| Traceability | Every inspection stores operator, equipment, lot, wafer, recipe, source, model version and variant, latency and reviews. Full audit log of logins, inspections, reviews, exports and config changes. |
| Security | JWT login, 4-level RBAC (Operator < Engineer < Manager < Admin), API keys for MES/ERP (`X-API-Key`), PBKDF2 password hashing |
| Data | PostgreSQL (production) or SQLite (desktop/edge). Images on a volume, metadata in the DB. |
| Integration | REST API with OpenAPI 3 schema, WebSocket event stream |
| Exports | PDF inspection report, PDF lot/period summary, CSV, ZIP of annotated images + masks, audit CSV |
| MLOps | Model manifest versioning, MLflow logging, champion/challenger A/B testing (shadow or traffic split), hot reload and promotion |
| Observability | Prometheus `/metrics`, Grafana dashboard, Prometheus alert rules, JSON logs (ELK/Loki-ready) |
| Deployment | Docker Compose, Kubernetes (HPA, PDB, ingress, optional KEDA), Terraform (AWS: VPC, EKS, RDS, ElastiCache, EFS, ECR, S3), Jetson TensorRT image, OpenVINO profile, desktop installers |

---

## Repository layout

```
waferguard/
  data/          image I/O (all formats), canonical preprocessing, dataset indexing + leakage-safe split, synthetic maps
  inference/     ONNX Runtime ensemble engine, geometry features, localization, severity
  training/      architectures, augmentation + focal loss + hard-negative mining, train pipeline, Optuna tuning
  api/           FastAPI app: routers/ (auth, inspections, analytics, system), services/ (alerts, camera, jobs,
                 events, models A/B, reports, SPC, observability), db.py, security.py, worker.py
  desktop/       desktop launcher (local server + native window)
frontend/        React + Vite UI (built into waferguard/api/static)
models/          wafer-ensemble/ (shipped ensemble: manifest, ONNX, Keras, GBM), wafer-ensemble-int8/
deploy/          docker/, k8s/, terraform/aws/, configs/ (train, onprem, cloud, edge-jetson, edge-openvino, desktop),
                 monitoring/ (Prometheus, Grafana)
packaging/       PyInstaller spec, Inno Setup script, AppImage files
scripts/         run_local, evaluate, quantize_int8, export_tensorrt, export_openvino, build_desktop_*, deploy_k8s
sample_data/     27 held-out real wafer maps + one file per supported format
tests/           pytest suite (56 tests)
docs/            ARCHITECTURE, API, TRAINING, DEPLOYMENT, TROUBLESHOOTING, MODEL_CARD
```

## Tests

```
pip install -r requirements-dev.txt
pytest --cov=waferguard
```

The suite has 56 tests, all passing with the shipped model. Coverage is **97%
of the serving code**. The TensorFlow training package, the desktop GUI launcher and the
blocking worker loop are excluded from the coverage figure (`pyproject.toml`). They need
TensorFlow, a display or a live Redis. The worker's job-processing logic *is* tested,
through `consume_forever` with fakeredis.

## Documentation

- `docs/ARCHITECTURE.md`: components, data flow, design decisions
- `docs/API.md`: authentication, endpoints, MES integration examples
- `docs/TRAINING.md`: dataset format, retraining, custom taxonomies, GPU backbones, tuning, evaluation
- `docs/DEPLOY_QUICKSTART.md`: step-by-step deployment (Windows/LAN and internet with HTTPS)
- `docs/DEPLOYMENT.md`: Docker, on-prem, Kubernetes, AWS / GCP / Azure, edge (Jetson, OpenVINO), desktop installers
- `docs/TROUBLESHOOTING.md`: common failures and fixes
- `docs/MODEL_CARD.md`: intended use, data, metrics, limitations

## Dataset (not included in this archive)

The 446 MB image dataset from the original project is not repackaged. Copy your existing
`wafer-cnn-app/dataset/` folder (9 class sub-folders) to `./dataset/` to retrain or evaluate.
The leakage-safe split is recomputed from the images, so results are reproducible (seed 42).
