#!/usr/bin/env bash
# NVIDIA Jetson / dGPU: build and cache TensorRT engines for every ONNX member, then benchmark.
# Requires onnxruntime-gpu with TensorRT EP (see deploy/docker/Dockerfile.jetson). Optional: trtexec.
set -euo pipefail
MODEL_DIR=${1:-models/wafer-ensemble}
export WG_TRT_CACHE=${WG_TRT_CACHE:-$MODEL_DIR/trt_cache}
export WG_TRT_FP16=${WG_TRT_FP16:-1}

echo "Building TensorRT engines (FP16=$WG_TRT_FP16) into $WG_TRT_CACHE ..."
python3 - "$MODEL_DIR" <<'PY'
import sys, time, numpy as np
from waferguard.inference.engine import InferenceEngine
eng = InferenceEngine(sys.argv[1], providers=["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"], tta=False)
print("providers:", eng.providers)
x = np.random.rand(1, eng.input_size, eng.input_size, 3).astype("float32")
eng.predict_tensors(x)                      # first call builds + caches the engines
t = time.perf_counter(); n = 200
for _ in range(n): eng.predict_tensors(x)
print(f"ensemble latency: {(time.perf_counter()-t)/n*1000:.2f} ms/wafer (batch 1, no TTA)")
PY

if command -v trtexec >/dev/null; then
  for f in "$MODEL_DIR"/*.onnx; do
    echo "trtexec benchmark: $f"
    trtexec --onnx="$f" --fp16 --shapes=input:1x64x64x3 --saveEngine="${f%.onnx}.plan" --duration=5 | grep -E "Throughput|mean" || true
  done
fi
echo "Done. Start the server with WG_CONFIG=deploy/configs/edge-jetson.yaml"
