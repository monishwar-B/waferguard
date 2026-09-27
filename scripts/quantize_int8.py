"""Post-training INT8 quantization of the ONNX members for CPU / OpenVINO edge boxes.

    python scripts/quantize_int8.py --model-dir models/wafer-ensemble --calib dataset --out models/wafer-ensemble-int8

Writes a complete model directory (manifest + quantized ONNX + GBM member) that the
server can load directly or use as an A/B challenger. Always compare accuracy with
`python scripts/evaluate.py` before promoting it.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # run from anywhere

import argparse
import json
import os
import random
import shutil

import numpy as np
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
from onnxruntime.quantization.shape_inference import quant_pre_process

from waferguard.data.io import is_supported, load_image_path
from waferguard.data.preprocess import preprocess


class Reader(CalibrationDataReader):
    def __init__(self, files, input_name, size):
        self.it = iter([{input_name: preprocess(load_image_path(f), size)[0][None]} for f in files])

    def get_next(self):
        return next(self.it, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--calib", required=True, help="folder of representative images (any layout)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=300)
    a = ap.parse_args()
    man = json.load(open(os.path.join(a.model_dir, "manifest.json")))
    files = [os.path.join(r, f) for r, _, fs in os.walk(a.calib) for f in fs if is_supported(f)]
    random.Random(0).shuffle(files)
    os.makedirs(a.out, exist_ok=True)
    import onnxruntime as ort
    for m in man["members"]:
        src = os.path.join(a.model_dir, m["file"])
        if m["type"] != "onnx":
            shutil.copy(src, os.path.join(a.out, m["file"]))
            continue
        pre = os.path.join(a.out, "tmp_pre.onnx")
        quant_pre_process(src, pre)
        name = ort.InferenceSession(src, providers=["CPUExecutionProvider"]).get_inputs()[0].name
        quantize_static(pre, os.path.join(a.out, m["file"]), Reader(files[: a.n], name, man["input_size"]),
                        quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                        per_channel=True)
        os.remove(pre)
        print("quantized", m["file"])
    man["version"] = man["version"] + "-int8"
    man.pop("test_metrics", None)  # must be re-measured
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), indent=2)
    print("done ->", a.out)


if __name__ == "__main__":
    main()
