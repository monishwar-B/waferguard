# Architecture

```
            ┌──────────── browsers / kiosks / tablets (React UI) ───────────┐
            │  REST (JWT)                             WebSocket /ws         │
MES / ERP ──┤  REST (X-API-Key)                                             │
            ▼                                                               │
   ┌───────────────────────────── FastAPI (N replicas) ────────────────────┴─┐
   │ routers: auth · inspections · jobs · exports · spc · alerts · audit ·  │
   │          models (A/B) · cameras · health/metrics                        │
   │ services: InspectionService ─► ModelRegistry ─► InferenceEngine (ORT)   │
   │           AlertEngine ─► Notifier (SMTP / Teams / Twilio / webhook)     │
   │           CameraManager (threads, 1–30 FPS)   EventBus ◄──┐             │
   └───────┬──────────────┬───────────────────┬────────────────┼────────────┘
           │              │ jobs (wg:jobs)    │ events (wg:events pub/sub)
           ▼              ▼                   │
     PostgreSQL        Redis ◄────────────────┘
     (metadata,          │
      audit, alerts)     ▼
           ▲       Batch workers (N) ── same InspectionService / engine
           │              │
           └──────────────┴──► image volume (source, annotated, mask PNGs)
```

## Request path for one wafer

1. **Decode** (`data/io.py`): any supported format to a numpy array.
2. **Canonicalize** (`data/preprocess.py`): convert to a 3-level die map (off-wafer / pass / fail),
   crop to the wafer, and area-resample to a 64×64×3 one-hot tensor. Training uses the *same*
   function, so there is no train/serve skew. The input domain is detected and reported:
   colour-coded map, level map, or optical camera frame (heuristic conversion, flagged in
   the result).
3. **Ensemble** (`inference/engine.py`): ONNX CNN members (with 8-way dihedral TTA) plus
   the geometry-feature GBM, combined by weighted soft vote. ONNX Runtime picks
   TensorRT → CUDA → OpenVINO → CPU from what is installed.
4. **Localize** (`inference/localization.py`): cluster the failing dies into boxes,
   polygons and a mask. Isolated background fails are filtered out.
5. **Grade** (`inference/severity.py`): take the max of the pattern's base severity and the
   fail-ratio grade. Low confidence sets `needs_review`.
6. **Persist**: the DB row holds all metadata. Source, annotated and mask images go to storage.
7. **Alert and broadcast**: rules run per equipment, and events go to every WebSocket.

## Key design decisions

- **Serving has no TensorFlow dependency.** Models are exported to ONNX, so the runtime
  image is small and the same artifact runs on x86, Jetson and Intel edge boxes.
- **The class list lives in the model manifest**, not in code. A die-level SEM dataset
  with other classes flows through the whole system after retraining.
- **A leakage-safe split is the default** (see README). A model is only as good as its
  evaluation.
- **Images live on a file volume, not in the database.** Keeping large blobs out of
  Postgres keeps backups and queries fast. The volume can be EFS, Filestore, Azure
  Files or a local disk.
- **Two queue back-ends share one interface.** The desktop and edge builds run without
  Redis. Servers scale workers horizontally.
- **The A/B framework is built into serving.** In shadow mode a new model is judged on
  live wafers with zero operator impact. Engineer reviews give per-variant accuracy.

## Data model (tables)

`users`, `api_keys`, `inspections` (all traceability fields, results, regions JSON,
probabilities JSON, shadow result, review fields), `jobs`, `alerts`, `audit_log`.
Tables are created automatically on start. For schema migrations in production, add
Alembic on top of `waferguard/api/db.py` (the models are plain SQLAlchemy 2.0).
