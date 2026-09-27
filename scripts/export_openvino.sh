#!/usr/bin/env bash
# Intel x86 edge: run the ONNX members through OpenVINO (via onnxruntime-openvino) and benchmark.
# Optional IR conversion for OpenVINO-native deployments (pip install openvino).
set -euo pipefail
MODEL_DIR=${1:-models/wafer-ensemble}
DEVICE=${WG_OPENVINO_DEVICE:-AUTO}
export WG_OPENVINO_DEVICE=$DEVICE

python3 - "$MODEL_DIR" <<'PY'
import sys, time, numpy as np
from waferguard.inference.engine import InferenceEngine
eng = InferenceEngine(sys.argv[1], providers=["OpenVINOExecutionProvider", "CPUExecutionProvider"], tta=False)
print("providers:", eng.providers)
x = np.random.rand(1, eng.input_size, eng.input_size, 3).astype("float32")
eng.predict_tensors(x)
t = time.perf_counter(); n = 200
for _ in range(n): eng.predict_tensors(x)
print(f"ensemble latency: {(time.perf_counter()-t)/n*1000:.2f} ms/wafer")
PY

if command -v ovc >/dev/null; then
  mkdir -p "$MODEL_DIR/openvino_ir"
  for f in "$MODEL_DIR"/*.onnx; do
    ovc "$f" --output_model "$MODEL_DIR/openvino_ir/$(basename "${f%.onnx}").xml" --compress_to_fp16
  done
  echo "IR written to $MODEL_DIR/openvino_ir (benchmark: benchmark_app -m <xml> -d $DEVICE)"
fi
echo "For INT8 on CPU: python scripts/quantize_int8.py --model-dir $MODEL_DIR --calib dataset --out ${MODEL_DIR}-int8"
