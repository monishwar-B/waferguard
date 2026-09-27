#!/usr/bin/env bash
# Build, push and deploy to a Kubernetes cluster (EKS/GKE/AKS).
#   REGISTRY=123456789.dkr.ecr.ap-south-1.amazonaws.com ./scripts/deploy_k8s.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${REGISTRY:?set REGISTRY to your container registry}"
TAG=${TAG:-1.0.0}
docker build -f deploy/docker/Dockerfile -t "$REGISTRY/waferguard:$TAG" .
docker push "$REGISTRY/waferguard:$TAG"
cd deploy/k8s
kustomize edit set image "REGISTRY/waferguard=$REGISTRY/waferguard:$TAG" 2>/dev/null || \
  sed -i "s#REGISTRY/waferguard:1.0.0#$REGISTRY/waferguard:$TAG#g" 03-api.yaml 04-worker.yaml
kubectl apply -k .
kubectl -n waferguard rollout status deploy/waferguard-api
