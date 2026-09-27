# Deployment guide

All deployments run the same code and model directory. Behaviour is selected with a profile,
`WG_CONFIG=deploy/configs/<profile>.yaml`, and environment variables (`WG_*`, nested keys use
`__`, e.g. `WG_ALERTS__SPIKE_RATE=0.4`). Environment variables override the profile.

| Profile | Database | Queue | Inference | Use |
|---|---|---|---|---|
| `onprem` | PostgreSQL | Redis + workers | CPU / CUDA | fab server, docker compose |
| `cloud` | managed Postgres | managed Redis | CPU pods (optionally GPU nodes) | Kubernetes |
| `edge-jetson` | SQLite | in-process | TensorRT FP16 | at the inspection tool |
| `edge-openvino` | SQLite | in-process | OpenVINO (CPU/iGPU) | Intel IPC at the tool |
| `desktop` | SQLite (per user) | in-process | CPU | engineering workstation |

**Security checklist for every server deployment:** set `WG_JWT_SECRET` (long and random),
`WG_BOOTSTRAP_ADMIN_PASSWORD` (or change admin's password at first login), and
`POSTGRES_PASSWORD`. Terminate TLS at the ingress or reverse proxy. Restrict `cors_origins`.

---

## 1. Docker Compose (on-prem server): one command

```bash
cp .env.example .env    # edit the secrets
docker compose up -d                      # api, worker, postgres, redis, prometheus, grafana
docker compose up -d --scale worker=4     # more batch throughput
docker compose --profile mlops up -d      # + MLflow on :5000
```

- UI and API: http://server:8000. Grafana: :3000 (dashboard "WaferGuard line overview"). Prometheus: :9090.
- Data lives in named volumes: `pgdata`, `images`, `redisdata`. Back up with `pg_dump` + a copy of `images`.
- Models are mounted read-only from `./models`. To add a challenger, copy a new folder there and set it in the UI.
- **Cameras** attached to the server: add `devices: ["/dev/video0:/dev/video0"]` to the `api` service.
  GigE Vision cameras need `network_mode: host` and a GStreamer/Aravis-enabled OpenCV build, or
  the `harvesters` package plus the vendor's GenTL `.cti` producer mounted into the container.

## 2. Kubernetes (any cloud)

```bash
REGISTRY=<registry> ./scripts/deploy_k8s.sh
```

`deploy/k8s/` contains:
- namespace, ConfigMap and Secret (**edit the Secret**, or use External Secrets)
- a ReadWriteMany PVC for images
- API Deployment with probes, a CPU HPA (2–10 replicas) and a PodDisruptionBudget
- worker Deployment with an HPA (or the optional KEDA ScaledObject that scales on Redis queue length)
- an NGINX Ingress with WebSocket and large-upload timeouts, and TLS via cert-manager

WebSocket events and batch jobs work across replicas through Redis pub/sub and the queue.
Run cameras on-prem (edge profile) rather than in the cloud.

### AWS (Terraform)

```bash
cd deploy/terraform/aws
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
aws eks update-kubeconfig --name $(terraform output -raw cluster_name)
terraform output -raw database_url     # put this and redis_url into deploy/k8s/01-config.yaml
# set fileSystemId in deploy/k8s/02a-efs-storageclass.yaml from `terraform output efs_id`, then:
kubectl apply -f ../../k8s/02a-efs-storageclass.yaml
REGISTRY=$(terraform output -raw ecr_repository_url | cut -d/ -f1) ../../../scripts/deploy_k8s.sh
```

This creates: VPC (3 AZ), EKS + managed node group (with the EFS CSI add-on), RDS PostgreSQL 16
(encrypted, Multi-AZ in prod, 14-day backups), ElastiCache Redis 7 (replicated), EFS for images,
ECR, and a versioned private S3 bucket for model artifacts and exports.
For GPU inference, add a `g5.xlarge` node group and use an image with `onnxruntime-gpu`.

> The Terraform code was written for this project but could not be applied from the
> build environment (no cloud credentials). Run `terraform plan` and review it before applying.

### GCP (GKE)

```bash
gcloud container clusters create-auto waferguard --region=asia-south1
gcloud sql instances create waferguard --database-version=POSTGRES_16 --region=asia-south1 --tier=db-custom-2-7680
gcloud redis instances create waferguard --size=1 --region=asia-south1
gcloud filestore instances create waferguard --zone=asia-south1-a --tier=BASIC_HDD --file-share=name=images,capacity=1TB
```

In `02-storage.yaml`, set `storageClassName: standard-rwx` (Filestore CSI). Push the image to
Artifact Registry, put the Cloud SQL and Memorystore addresses in the Secret, then run `kubectl apply -k deploy/k8s`.
Use the Cloud SQL Auth Proxy sidecar or private IP.

### Azure (AKS)

```bash
az aks create -g waferguard -n waferguard --node-count 2 --enable-cluster-autoscaler --min-count 2 --max-count 8
az postgres flexible-server create -g waferguard -n waferguard-db --version 16
az redis create -g waferguard -n waferguard-cache --sku Standard --vm-size c1
```

In `02-storage.yaml`, set `storageClassName: azurefile-csi`. Push the image to ACR
(`az aks update --attach-acr`), fill in the Secret, then run `kubectl apply -k deploy/k8s`.

## 3. Edge

### NVIDIA Jetson (Orin / Xavier, JetPack 6)

```bash
docker build -f deploy/docker/Dockerfile.jetson -t waferguard:jetson .      # on the Jetson
docker run --runtime nvidia -p 8000:8000 -v wgdata:/data --device /dev/video0 \
    -e WG_CONFIG=deploy/configs/edge-jetson.yaml -e WG_JWT_SECRET=... waferguard:jetson
./scripts/export_tensorrt.sh models/wafer-ensemble      # build + cache FP16 engines, benchmark
```

The first start builds TensorRT engines (one to a few minutes). They are cached in
`models/wafer-ensemble/trt_cache`, so later starts are instant.

### Intel x86 (OpenVINO)

```bash
pip install -r requirements.txt && pip uninstall -y onnxruntime && pip install onnxruntime-openvino
WG_CONFIG=deploy/configs/edge-openvino.yaml WG_OPENVINO_DEVICE=GPU ./scripts/run_local.sh
./scripts/export_openvino.sh                            # benchmark; optional IR conversion
python scripts/quantize_int8.py --model-dir models/wafer-ensemble --calib dataset --out models/wafer-ensemble-int8
python scripts/evaluate.py --model-dir models/wafer-ensemble-int8 --data-root dataset --split test   # verify before use
```

## 4. Desktop application

End users get installers you build once per OS:

| OS | Build on | Command | Output |
|---|---|---|---|
| Windows 10/11 x64 | Windows | `.\scripts\build_desktop_windows.ps1` (Python 3.12, Node 20, Inno Setup 6) | `dist\WaferGuard-Setup-1.0.0.exe` |
| Linux x86_64 | oldest distro you support (e.g. Ubuntu 20.04) | `./scripts/build_desktop_linux.sh` | `dist/WaferGuard-1.0.0-x86_64.AppImage` |

The app starts the full server on 127.0.0.1 and opens it in a native window (pywebview, or the
default browser as fallback). Data is kept in `%LOCALAPPDATA%\WaferGuard` or `~/.local/share/WaferGuard`.
Installers could not be produced in the build environment. PyInstaller cannot cross-compile, so
each installer must be built on its target OS; the scripts do this in one step.
Without building an installer, from source: `python -m waferguard.desktop.launcher`.

## 5. Monitoring and logging

- **Prometheus metrics** at `/metrics`: `wg_inspections_total{label,severity,source,variant}`,
  `wg_inference_latency_seconds`, `wg_alerts_total`, `wg_queue_depth`, `wg_camera_fps`,
  `wg_websocket_clients`, `wg_http_request_seconds`, `wg_batch_jobs_total`.
- **Grafana dashboard and Prometheus alert rules** are provisioned automatically by compose (`deploy/monitoring/`).
- **ELK:** set `WG_LOG_JSON=true` (the default in the server profiles). Each log line is one JSON object.
  Ship container stdout with Filebeat (`filebeat.autodiscover` + `decode_json_fields`) or Fluent Bit to Elasticsearch.
