"""Model registry and A/B testing.

* ``off``     only the champion model serves.
* ``shadow``  the challenger also scores every wafer; its answer is stored with
              the inspection (``shadow`` column) but never shown to operators.
              Agreement and reviewed-accuracy statistics tell you whether to promote it.
* ``split``   a share of traffic (``ab_split``) is served by the challenger.

Promotion swaps directories at runtime without restarting the server.
"""
from __future__ import annotations

import logging
import random
import threading

from waferguard.inference.engine import InferenceEngine, ModelLoadError
from waferguard.inference.severity import SeverityPolicy

log = logging.getLogger("waferguard.models")


class ModelRegistry:
    def __init__(self, settings, policy: SeverityPolicy):
        self.settings = settings.models
        self.policy = policy
        self._lock = threading.RLock()
        self.champion: InferenceEngine | None = None
        self.challenger: InferenceEngine | None = None
        self.load_error: str | None = None
        self.mode = self.settings.ab_mode
        self.split = self.settings.ab_split
        self._rng = random.Random()
        self.reload()

    def _load(self, path: str | None) -> InferenceEngine | None:
        if not path:
            return None
        return InferenceEngine(path, self.settings.providers, self.settings.tta, self.policy, self.settings.threads)

    def reload(self) -> None:
        with self._lock:
            try:
                self.champion = self._load(self.settings.champion_dir)
                self.load_error = None
            except (ModelLoadError, OSError) as exc:
                self.champion, self.load_error = None, str(exc)
                log.error("champion model not loaded: %s", exc)
            try:
                self.challenger = self._load(self.settings.challenger_dir)
            except (ModelLoadError, OSError) as exc:
                self.challenger = None
                log.error("challenger model not loaded: %s", exc)

    @property
    def ready(self) -> bool:
        return self.champion is not None

    def configure(self, mode: str | None = None, split: float | None = None, challenger_dir: str | None = None) -> None:
        with self._lock:
            if challenger_dir is not None:
                engine = self._load(challenger_dir or None)  # raises before any state changes
                self.settings.challenger_dir = challenger_dir or None
                self.challenger = engine
            if mode is not None:
                if mode not in ("off", "shadow", "split"):
                    raise ValueError("mode must be off, shadow or split")
                self.mode = mode
            if split is not None:
                if not 0 <= split <= 1:
                    raise ValueError("split must be between 0 and 1")
                self.split = split

    def promote(self) -> None:
        with self._lock:
            if self.challenger is None:
                raise ValueError("no challenger loaded")
            self.settings.champion_dir, self.settings.challenger_dir = self.settings.challenger_dir, self.settings.champion_dir
            self.champion, self.challenger = self.challenger, self.champion
            self.mode = "off"

    def route(self) -> tuple[InferenceEngine, str, InferenceEngine | None]:
        """Returns (serving engine, variant name, shadow engine or None)."""
        with self._lock:
            if self.champion is None:
                raise ModelLoadError(self.load_error or "no model loaded")
            if self.challenger is not None and self.mode == "split" and self._rng.random() < self.split:
                return self.challenger, "challenger", None
            if self.challenger is not None and self.mode == "shadow":
                return self.champion, "champion", self.challenger
            return self.champion, "champion", None

    def info(self) -> dict:
        with self._lock:
            return {
                "ready": self.ready,
                "error": self.load_error,
                "ab_mode": self.mode,
                "ab_split": self.split,
                "champion": self.champion.info() if self.champion else None,
                "challenger": self.challenger.info() if self.challenger else None,
            }
