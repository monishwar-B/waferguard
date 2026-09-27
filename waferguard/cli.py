"""Command line interface.

    waferguard serve   [--host 0.0.0.0 --port 8000]      run API + web UI
    waferguard worker                                    run a Redis batch worker
    waferguard train   --config deploy/configs/train.yaml
    waferguard predict <folder|file> [--out results.csv] offline inspection, no server needed
    waferguard info    [--model-dir DIR]                 show model manifest / providers
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys


def _predict(args):
    from waferguard.data.io import is_supported, load_image_path
    from waferguard.inference.engine import InferenceEngine
    eng = InferenceEngine(args.model_dir, args.providers, None if args.tta is None else args.tta == "on")
    files = [args.path] if os.path.isfile(args.path) else sorted(
        os.path.join(r, f) for r, _, fs in os.walk(args.path) for f in fs if is_supported(f))
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(["file", "label", "confidence", "severity", "needs_review", "fail_ratio", "n_regions", "input_domain"])
    for i in range(0, len(files), args.batch):
        chunk = files[i:i + args.batch]
        for f, r in zip(chunk, eng.analyze_batch([load_image_path(p) for p in chunk])):
            w.writerow([f, r["label"], f"{r['confidence']:.4f}", r["severity"], r["needs_review"],
                        f"{r['fail_ratio']:.4f}", len(r["regions"]), r["input_domain"]])
    if args.out:
        out.close()
        print(f"wrote {len(files)} results to {args.out}")


def main(argv=None):
    from waferguard.api.settings import ModelSettings
    default_model = ModelSettings().champion_dir
    ap = argparse.ArgumentParser(prog="waferguard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1)
    sub.add_parser("worker")
    t = sub.add_parser("train")
    t.add_argument("rest", nargs=argparse.REMAINDER)
    p = sub.add_parser("predict")
    p.add_argument("path")
    p.add_argument("--out")
    p.add_argument("--model-dir", default=default_model)
    p.add_argument("--providers", default="auto")
    p.add_argument("--tta", choices=["on", "off"])
    p.add_argument("--batch", type=int, default=32)
    i = sub.add_parser("info")
    i.add_argument("--model-dir", default=default_model)
    args = ap.parse_args(argv)
    if args.cmd == "serve":  # pragma: no cover
        import uvicorn
        uvicorn.run("waferguard.api.main:app", host=args.host, port=args.port, workers=args.workers)
    elif args.cmd == "worker":  # pragma: no cover
        from waferguard.api.worker import main as w
        w()
    elif args.cmd == "train":  # pragma: no cover
        from waferguard.training.train import main as tr
        tr(args.rest)
    elif args.cmd == "predict":
        _predict(args)
    elif args.cmd == "info":
        from waferguard.inference.engine import InferenceEngine
        print(json.dumps(InferenceEngine(args.model_dir).info(), indent=2))


if __name__ == "__main__":
    main()
