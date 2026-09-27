"""Inference engine: weighted-vote ensemble over ONNX + scikit-learn members.

Serving depends only on numpy / OpenCV / onnxruntime / scikit-learn, never on
TensorFlow. Execution providers are chosen automatically (TensorRT -> CUDA ->
OpenVINO -> CPU) from what the installed onnxruntime build supports, so the same
model directory runs on a Jetson (onnxruntime-gpu with TensorRT), an Intel
edge box (onnxruntime-openvino) or a plain x86 server.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass

import numpy as np

from waferguard.data.preprocess import dihedral, preprocess
from waferguard.inference import features as feat
from waferguard.inference.localization import find_regions
from waferguard.inference.severity import SeverityPolicy, grade
from waferguard.taxonomy import PROBABLE_CAUSES, display_name

PROVIDER_PREFERENCE = [
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "OpenVINOExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
]


class ModelLoadError(RuntimeError):
    pass


def select_providers(requested: str | list[str] | None = "auto") -> list[str]:
    import onnxruntime as ort
    available = ort.get_available_providers()
    if requested in (None, "auto"):
        return [p for p in PROVIDER_PREFERENCE if p in available] or available
    req = [requested] if isinstance(requested, str) else list(requested)
    chosen = [p for p in req if p in available]
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


@dataclass
class Member:
    name: str
    kind: str
    weight: float
    runner: object
    input_name: str | None = None

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.kind == "onnx":
            return np.asarray(self.runner.run(None, {self.input_name: x.astype(np.float32)})[0])
        return np.asarray(self.runner.predict_proba(feat.extract_batch(x)))


class InferenceEngine:
    def __init__(self, model_dir: str, providers: str | list[str] | None = "auto", tta: bool | None = None,
                 policy: SeverityPolicy | None = None, threads: int | None = None):
        self.model_dir = model_dir
        path = os.path.join(model_dir, "manifest.json")
        if not os.path.exists(path):
            raise ModelLoadError(f"no manifest.json in {model_dir}")
        with open(path) as fh:
            self.manifest = json.load(fh)
        self.classes: list[str] = self.manifest["classes"]
        self.input_size: int = int(self.manifest["input_size"])
        self.version: str = self.manifest.get("version", "unknown")
        self.tta = self.manifest.get("tta", True) if tta is None else tta
        self.policy = policy or SeverityPolicy()
        self.providers = select_providers(providers)
        self._lock = threading.Lock()
        self.members: list[Member] = []
        for m in self.manifest["members"]:
            f = os.path.join(model_dir, m["file"])
            if not os.path.exists(f):
                raise ModelLoadError(f"member file missing: {f}")
            if m["type"] == "onnx":
                import onnxruntime as ort
                so = ort.SessionOptions()
                if threads:
                    so.intra_op_num_threads = threads
                sess = ort.InferenceSession(f, sess_options=so, providers=self._provider_options())
                self.members.append(Member(m["name"], "onnx", float(m.get("weight", 1.0)), sess, sess.get_inputs()[0].name))
            elif m["type"] == "portable_gbm":
                from waferguard.inference.portable_gbm import PortableHGB
                self.members.append(Member(m["name"], "sklearn", float(m.get("weight", 1.0)), PortableHGB(f)))
            elif m["type"] == "sklearn":
                portable = os.path.splitext(f)[0] + ".npz"
                if os.path.exists(portable):  # prefer the version-independent export when present
                    from waferguard.inference.portable_gbm import PortableHGB
                    runner = PortableHGB(portable)
                else:
                    import joblib
                    try:
                        runner = joblib.load(f)
                    except Exception as exc:  # noqa: BLE001 - usually a scikit-learn version mismatch
                        raise ModelLoadError(
                            f"cannot load {f} ({exc.__class__.__name__}: {exc}). It was saved by a different "
                            "scikit-learn version; convert it once with: python scripts/convert_gbm.py "
                            f"{model_dir}") from exc
                self.members.append(Member(m["name"], "sklearn", float(m.get("weight", 1.0)), runner))
            else:
                raise ModelLoadError(f"unknown member type {m['type']}")
        total = sum(mb.weight for mb in self.members) or 1.0
        for mb in self.members:
            mb.weight /= total

    def _provider_options(self):
        """TensorRT: FP16 + on-disk engine cache (first start builds engines, later starts are instant)."""
        out = []
        for p in self.providers:
            if p == "TensorrtExecutionProvider":
                cache = os.environ.get("WG_TRT_CACHE", os.path.join(self.model_dir, "trt_cache"))
                os.makedirs(cache, exist_ok=True)
                out.append((p, {"trt_fp16_enable": os.environ.get("WG_TRT_FP16", "1") == "1",
                                "trt_engine_cache_enable": True, "trt_engine_cache_path": cache}))
            elif p == "OpenVINOExecutionProvider":
                out.append((p, {"device_type": os.environ.get("WG_OPENVINO_DEVICE", "AUTO")}))
            else:
                out.append(p)
        return out

    # ------------------------------------------------------------------ core
    def predict_tensors(self, x: np.ndarray, tta: bool | None = None) -> np.ndarray:
        """x: (N, S, S, 3) -> (N, K) ensemble probabilities."""
        use_tta = self.tta if tta is None else tta
        views = [dihedral(x, k) for k in range(8)] if use_tta else [x]
        out = np.zeros((len(x), len(self.classes)), np.float64)
        for mb in self.members:
            if mb.weight == 0:
                continue
            # geometry features are rotation-robust already: TTA only for the CNNs
            vs = views if mb.kind == "onnx" else [x]
            out += mb.weight * np.mean([mb.predict(v) for v in vs], axis=0)
        return out / out.sum(1, keepdims=True)

    def analyze(self, image: np.ndarray, tta: bool | None = None, localize: bool = True) -> dict:
        t0 = time.perf_counter()
        tensor, levels, domain = preprocess(image, self.input_size)
        probs = self.predict_tensors(tensor[None], tta)[0]
        return self._result(probs, tensor, levels, domain, localize, t0)

    def analyze_batch(self, images: list[np.ndarray], tta: bool | None = None, localize: bool = True) -> list[dict]:
        t0 = time.perf_counter()
        pre = [preprocess(im, self.input_size) for im in images]
        probs = self.predict_tensors(np.stack([p[0] for p in pre]), tta)
        return [self._result(pr, t, lv, d, localize, t0, len(images)) for pr, (t, lv, d) in zip(probs, pre)]

    def _result(self, probs, tensor, levels, domain, localize, t0, n=1) -> dict:
        i = int(np.argmax(probs))
        label, conf = self.classes[i], float(probs[i])
        wafer = levels > 0
        fail_ratio = float((levels == 2).sum() / max(int(wafer.sum()), 1))
        sev = grade(label, conf, fail_ratio, self.policy)
        regions, mask = find_regions(levels, label) if localize else ([], None)
        top = np.argsort(probs)[::-1][:3]
        return {
            "label": label,
            "display_name": display_name(label),
            "confidence": conf,
            "probabilities": {c: float(p) for c, p in zip(self.classes, probs)},
            "top3": [{"label": self.classes[j], "p": float(probs[j])} for j in top],
            "severity": sev["severity"],
            "needs_review": sev["needs_review"],
            "uncertain": sev["uncertain"],
            "severity_reasons": sev["reasons"],
            "fail_ratio": fail_ratio,
            "die_count": int(wafer.sum()),
            "regions": regions,
            "probable_causes": PROBABLE_CAUSES.get(label, []),
            "input_domain": domain,
            "domain_warning": (
                "Continuous-tone camera image: converted with a heuristic; the model was trained on wafer maps. "
                "Validate on your tool before relying on this result." if domain == "optical" else None),
            "model_version": self.version,
            "latency_ms": (time.perf_counter() - t0) * 1000.0 / n,
            "_levels": levels,   # internal: stripped by the API, used for rendering/export
            "_mask": mask,
        }

    def info(self) -> dict:
        tm = self.manifest.get("test_metrics", {})
        abl = tm.get("ablation_accuracy", {})
        by_name = {m["name"]: m for m in self.manifest.get("members", [])}
        members = []
        for m in self.members:
            spec = by_name.get(m.name, {})
            members.append({
                "name": m.name, "type": m.kind, "arch": spec.get("arch"), "params": spec.get("params", {}),
                "weight": round(m.weight, 4),
                # accuracy of this member alone on the held-out test split (None if not recorded)
                "test_accuracy": abl.get(m.name),
                "test_accuracy_tta": abl.get(f"{m.name}+tta"),
            })
        split = self.manifest.get("dataset", {}).get("split", {})
        return {
            "version": self.version,
            "classes": self.classes,
            "providers": self.providers,
            "tta": self.tta,
            "members": members,
            "test_metrics": {k: v for k, v in tm.items() if k in ("accuracy", "macro_f1", "nll")},
            "ensemble_no_tta_accuracy": abl.get("ensemble_no_tta"),
            "test_wafers": split.get("sizes", {}).get("test"),
            "trained_utc": self.manifest.get("created_utc"),
        }
