"""Live camera acquisition and real-time inspection.

Source strings:
  ``0``, ``1`` ...                   USB / UVC device index (most USB3 industrial cameras
                                     expose UVC or a V4L2 driver)
  ``rtsp://...`` / ``http://...``    network streams
  ``gst:<pipeline>``                 GStreamer pipeline ending in ``appsink``; e.g. GigE Vision
                                     through Aravis: ``gst:aravissrc camera-name=Basler-123 ! videoconvert ! appsink``
  ``genicam:<path/to/producer.cti>`` GenICam/GenTL (USB3 Vision + GigE Vision) via the optional
                                     ``harvesters`` package
  ``synthetic:<folder>``             replays images from a folder: demos and tests without hardware

Each camera runs on its own thread at a configurable rate (1-30 FPS). Every
frame is inspected; every Nth is persisted (``camera_store_every``); an
annotated JPEG thumbnail and the result are pushed to the WebSocket.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time

import cv2
import numpy as np

from waferguard.api.services.observability import CAMERA_FPS
from waferguard.data.io import is_supported, load_image_path
from waferguard.data.preprocess import to_levels
from waferguard.inference.localization import render

log = logging.getLogger("waferguard.camera")


class FrameSource:
    def read(self) -> np.ndarray | None:  # RGB uint8 or grayscale
        raise NotImplementedError

    def close(self) -> None:
        pass


class SyntheticSource(FrameSource):
    def __init__(self, folder: str):
        files = []
        for root, _, fs in os.walk(folder):
            files += [os.path.join(root, f) for f in sorted(fs) if is_supported(f)]
        if not files:
            raise ValueError(f"no images in {folder}")
        self.files, self.i = files, 0

    def read(self):
        f = self.files[self.i % len(self.files)]
        self.i += 1
        return load_image_path(f)


class OpenCVSource(FrameSource):
    def __init__(self, spec):
        if isinstance(spec, str) and spec.startswith("gst:"):
            self.cap = cv2.VideoCapture(spec[4:], cv2.CAP_GSTREAMER)
        else:
            self.cap = cv2.VideoCapture(spec)
        if not self.cap.isOpened():
            raise ValueError(f"cannot open camera source {spec!r}")

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame[..., ::-1].copy() if frame.ndim == 3 else frame

    def close(self):
        self.cap.release()


class GenICamSource(FrameSource):  # pragma: no cover - requires hardware
    def __init__(self, cti: str):
        from harvesters.core import Harvester
        self.h = Harvester()
        self.h.add_file(cti)
        self.h.update()
        if not self.h.device_info_list:
            raise ValueError("no GenICam devices found")
        self.ia = self.h.create(0)
        self.ia.start()

    def read(self):
        with self.ia.fetch(timeout=3) as buf:
            comp = buf.payload.components[0]
            return comp.data.reshape(comp.height, comp.width).copy()

    def close(self):
        self.ia.stop()
        self.ia.destroy()
        self.h.reset()


def open_source(spec: str) -> FrameSource:
    if spec.startswith("synthetic:"):
        return SyntheticSource(spec.split(":", 1)[1])
    if spec.startswith("genicam:"):
        return GenICamSource(spec.split(":", 1)[1])
    if spec.isdigit():
        return OpenCVSource(int(spec))
    return OpenCVSource(spec)


def probe_devices(max_index: int = 4) -> list[dict]:
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            found.append({"source": str(i), "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                          "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))})
        cap.release()
    return found


def thumbnail_b64(img, max_side: int = 320) -> str:
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        arr = cv2.resize(arr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", arr[..., ::-1] if arr.ndim == 3 else arr, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode()


class CameraWorker(threading.Thread):
    def __init__(self, state, cam_id: str, source: str, fps: float, meta: dict):
        super().__init__(name=f"wg-cam-{cam_id}", daemon=True)
        self.state, self.cam_id, self.source_spec = state, cam_id, source
        self.fps = max(1.0, min(30.0, float(fps)))
        self.meta = meta
        self.stop_event = threading.Event()
        self.frames = 0
        self.errors = 0
        self.effective_fps = 0.0
        self.last_error: str | None = None
        self.last_result: dict | None = None
        self.last_frame_b64: str | None = None
        self.started_at = time.time()
        self.src = open_source(source)  # raise early so the API can report a bad source

    def run(self):
        every = max(1, int(self.state.settings.camera_store_every))
        t_last = time.perf_counter()
        try:
            while not self.stop_event.is_set():
                t0 = time.perf_counter()
                frame = self.src.read()
                if frame is None:
                    self.errors += 1
                    self.last_error = "no frame"
                    if self.errors > 50:
                        break
                    time.sleep(0.2)
                    continue
                try:
                    self.frames += 1
                    meta = {**self.meta, "source": "camera", "store": self.frames % every == 0,
                            "filename": f"{self.cam_id}_{self.frames:06d}.png",
                            "wafer_id": self.meta.get("wafer_id") or f"{self.cam_id}-{self.frames:06d}"}
                    res = self.state.inspections.inspect(frame, meta)
                    self.last_result = {k: res.get(k) for k in ("id", "label", "confidence", "severity", "latency_ms",
                                                                 "needs_review", "input_domain")}
                    self.last_frame_b64 = thumbnail_b64(np.asarray(render(
                        to_levels(frame)[0], res["regions"], res["label"], res["severity"], res["confidence"],
                        frame if (res.get("input_domain") == "optical" and frame.ndim == 3 and frame.dtype == np.uint8)
                        else None, min_side=240)))
                    now = time.perf_counter()
                    inst = 1.0 / max(now - t_last, 1e-6)
                    t_last = now
                    self.effective_fps = inst if self.frames == 1 else 0.8 * self.effective_fps + 0.2 * inst
                    CAMERA_FPS.labels(self.cam_id).set(self.effective_fps)
                    self.state.events.publish("camera.frame", {"camera": self.cam_id, "frame": self.frames,
                                                               "fps": round(self.effective_fps, 2),
                                                               "result": self.last_result, "jpeg_b64": self.last_frame_b64})
                except Exception as exc:  # noqa: BLE001
                    self.errors += 1
                    self.last_error = f"{exc.__class__.__name__}: {exc}"
                    log.exception("camera %s frame failed", self.cam_id)
                self.stop_event.wait(max(0.0, 1.0 / self.fps - (time.perf_counter() - t0)))
        finally:
            self.src.close()
            CAMERA_FPS.labels(self.cam_id).set(0)

    def status(self) -> dict:
        return {"camera": self.cam_id, "source": self.source_spec, "target_fps": self.fps,
                "effective_fps": round(self.effective_fps, 2), "frames": self.frames, "errors": self.errors,
                "last_error": self.last_error, "running": self.is_alive(), "meta": self.meta,
                "uptime_s": round(time.time() - self.started_at, 1), "last_result": self.last_result}


class CameraManager:
    def __init__(self, state):
        self.state = state
        self.workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    def start(self, cam_id: str, source: str, fps: float, meta: dict) -> dict:
        with self._lock:
            old = self.workers.get(cam_id)
            if old and old.is_alive():
                raise ValueError(f"camera {cam_id} already running")
            w = CameraWorker(self.state, cam_id, source, fps, meta)
            self.workers[cam_id] = w
            w.start()
        self.state.events.publish("camera.status", w.status())
        return w.status()

    def stop(self, cam_id: str) -> dict:
        with self._lock:
            w = self.workers.get(cam_id)
        if not w:
            raise KeyError(cam_id)
        w.stop_event.set()
        w.join(timeout=5)
        st = w.status()
        self.state.events.publish("camera.status", st)
        return st

    def set_fps(self, cam_id: str, fps: float) -> dict:
        w = self.workers[cam_id]
        w.fps = max(1.0, min(30.0, float(fps)))
        return w.status()

    def status(self) -> list[dict]:
        return [w.status() for w in self.workers.values()]

    def stop_all(self):
        for cid in list(self.workers):
            try:
                self.stop(cid)
            except KeyError:  # pragma: no cover
                pass
