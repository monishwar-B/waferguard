# WaferGuard API / worker image (CPU, x86_64 or arm64).
# Build from the repository root:  docker build -f deploy/docker/Dockerfile -t waferguard:1.0.0 .

# ---- 1. frontend -------------------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ ./
RUN npx vite build --outDir /ui/dist

# ---- 2. runtime --------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    WG_STORAGE_DIR=/data/images WG_DATABASE_URL=sqlite:////data/waferguard.db
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home wg
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY waferguard/ waferguard/
COPY --from=ui /ui/dist/ waferguard/api/static/
COPY models/ models/
COPY deploy/configs/ deploy/configs/
COPY sample_data/ sample_data/
# create the volume mount points with the right owner: Docker copies this ownership into new named volumes
RUN mkdir -p /data/images && chown -R wg:wg /data /app
USER wg
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "waferguard.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
