# API reference

The complete, always-current schema is served by the application:
**`/docs`** (Swagger UI, try requests live), **`/redoc`**, and **`/openapi.json`**.
Import the JSON schema into Postman or an MES integration tool, or generate a client with
`openapi-generator`. This page covers the essentials.

## Authentication

| Client | How |
|---|---|
| People / UI | `POST /api/v1/auth/token` (form fields `username`, `password`) returns `{"access_token": ...}`. Send `Authorization: Bearer <token>`. Tokens last 12 h (`WG_TOKEN_MINUTES`). |
| MES / ERP / scripts | An Admin creates a key under Administration → API keys (or `POST /api/v1/api-keys`). Send `X-API-Key: wg_...`. The key carries a role. |
| `<img>` / download links | Append `?token=<jwt>` to image and export URLs. |
| WebSocket | `ws(s)://host/ws?token=<jwt>` |

Roles are hierarchical: **Operator < Engineer < Manager < Admin**. A 403 response names the role required.

## Endpoints

| Method & path | Min role | Purpose |
|---|---|---|
| `POST /api/v1/inspections` | Operator | Inspect one image (multipart `file`, optional `lot_id`, `wafer_id`, `equipment_id`, `recipe`, `raw_width`, `raw_height`, `raw_dtype`, `raw_channels`) |
| `POST /api/v1/inspections/frame` | Operator | Inspect a base64 frame (browser/kiosk camera) |
| `GET /api/v1/inspections` | Operator | List and filter: `lot_id, wafer_id, equipment_id, label, severity, operator, source, since, until, needs_review, job_id, limit, offset` |
| `GET /api/v1/inspections/{id}` | Operator | Full result: probabilities, regions, shadow result, review |
| `GET /api/v1/inspections/{id}/image/{source\|annotated\|mask}` | Operator | Stored images |
| `POST /api/v1/inspections/{id}/review` | Engineer | Confirm or correct the label (`{"label": "...", "note": "..."}`) |
| `POST /api/v1/jobs/batch` | Engineer | Queue many files and/or ZIPs (`files`, `lot_id`, `equipment_id`, ...). Returns 202 with a job id. |
| `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}` | Engineer / Operator | Job status and progress |
| `GET /api/v1/export/inspections.csv` | Engineer | CSV dump (same filters as the list) |
| `GET /api/v1/export/images.zip` | Engineer | Annotated images + masks + CSV |
| `GET /api/v1/export/inspections/{id}/report.pdf` | Operator | Inspection report |
| `GET /api/v1/export/summary.pdf` | Engineer | Lot / equipment / period summary with SPC |
| `GET /api/v1/spc/summary` | Operator | KPIs, Cpk/Ppk, Pareto |
| `GET /api/v1/spc/p-chart?group_by=hour\|shift\|day\|lot\|equipment` | Engineer | p-chart with limits and rule violations |
| `GET /api/v1/spc/imr` | Engineer | I-MR chart of the fail-die ratio + capability |
| `GET /api/v1/spc/trend`, `GET /api/v1/spc/equipment` | Operator | Trends and per-tool defect rates |
| `GET /api/v1/alerts`, `POST /api/v1/alerts/{id}/ack` | Operator / Engineer | Alerts |
| `GET/PUT /api/v1/alerts/rules`, `POST /api/v1/alerts/test` | Engineer / Manager | Rule configuration and channel test |
| `GET /api/v1/audit`, `GET /api/v1/audit.csv` | Manager | Audit trail |
| `GET /api/v1/models`, `PUT /api/v1/models/ab`, `POST /api/v1/models/promote`, `POST /api/v1/models/reload`, `GET /api/v1/models/ab/stats` | Operator / Admin / Engineer | Model info and A/B testing |
| `GET /api/v1/cameras`, `/devices`, `POST /api/v1/cameras/start`, `/{id}/stop`, `/{id}/fps` | Operator | Camera control |
| `GET/POST/PATCH /api/v1/users`, `/api-keys` | Admin | Users and keys |
| `GET /health`, `GET /ready`, `GET /metrics` | none | Probes and Prometheus |

## Inspection result (abridged)

```json
{
  "id": "5f0c…", "label": "Edge-Ring", "display_name": "Edge ring", "confidence": 0.973,
  "probabilities": {"none": 0.001, "Edge-Ring": 0.973, "...": 0.0},
  "severity": "Major", "needs_review": false, "severity_reasons": ["pattern 'Edge-Ring' baseline Major"],
  "fail_ratio": 0.142, "die_count": 2860,
  "regions": [{"bbox": [x, y, w, h], "area_px": 406, "area_frac": 0.142, "centroid": [cx, cy], "polygon": [[x, y], "..."]}],
  "probable_causes": ["Edge-bead removal problem", "CMP edge roll-off", "Etch uniformity at edge"],
  "input_domain": "wafer_map_rgb", "model_version": "2026.09.26-1512", "model_variant": "champion",
  "lot_id": "LOT-2419", "wafer_id": "W07", "equipment_id": "CMP-02", "latency_ms": 11.8, "alerts": []
}
```

## WebSocket events

`inspection.created`, `inspection.reviewed`, `job.progress`, `alert.raised`, `alert.acknowledged`,
`camera.frame` (includes a base64 JPEG thumbnail), `camera.status`, and `ping` every 20 s.
Every message has the shape `{"type": ..., "data": {...}}`.

## MES integration examples

```bash
# inspect a wafer map from the tool PC
curl -H "X-API-Key: $WG_KEY" -F file=@W07.tif -F lot_id=LOT-2419 -F wafer_id=W07 -F equipment_id=CMP-02 \
     https://waferguard.fab.local/api/v1/inspections

# raw 16-bit sensor dump
curl -H "X-API-Key: $WG_KEY" -F file=@frame.raw -F raw_width=2048 -F raw_height=2048 -F raw_dtype=uint16 \
     https://waferguard.fab.local/api/v1/inspections

# whole lot as ZIP, then poll
curl -H "X-API-Key: $WG_KEY" -F files=@LOT-2419.zip -F lot_id=LOT-2419 https://…/api/v1/jobs/batch
curl -H "X-API-Key: $WG_KEY" https://…/api/v1/jobs/<id>
```

```python
import httpx
c = httpx.Client(base_url="https://waferguard.fab.local", headers={"X-API-Key": KEY})
r = c.post("/api/v1/inspections", files={"file": open("W07.png", "rb")}, data={"lot_id": "LOT-2419"}).json()
if r["severity"] in ("Critical", "Major"):
    hold_lot(r["lot_id"])            # your MES call
```

For push-style integration, set `notifiers.webhook_url` so alerts are POSTed to your MES,
or consume the WebSocket stream.
