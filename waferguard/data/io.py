"""Image loading for every format the line may produce.

Supported: PNG, JPEG, BMP, TIFF (8/16-bit, multi-page -> first page),
NumPy .npy arrays (e.g. WM-811K raw die maps with values 0/1/2) and raw
sensor dumps (.raw/.bin) described by width/height/dtype.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
ARRAY_EXT = {".npy"}
RAW_EXT = {".raw", ".bin"}
SUPPORTED_EXT = RASTER_EXT | ARRAY_EXT | RAW_EXT


class ImageFormatError(ValueError):
    """Raised when an input cannot be decoded into an image array."""


@dataclass
class RawSpec:
    """Geometry of a headerless raw sensor dump."""
    width: int
    height: int
    dtype: str = "uint8"      # uint8 | uint16 | float32
    channels: int = 1
    byteorder: str = "little"

    def expected_bytes(self) -> int:
        return self.width * self.height * self.channels * np.dtype(self.dtype).itemsize


def _ext(name: str | None) -> str:
    return os.path.splitext(name or "")[1].lower()


def load_raw(data: bytes, spec: RawSpec) -> np.ndarray:
    if len(data) != spec.expected_bytes():
        raise ImageFormatError(
            f"raw buffer is {len(data)} bytes; {spec.width}x{spec.height}x{spec.channels} "
            f"{spec.dtype} needs {spec.expected_bytes()} bytes"
        )
    dt = np.dtype(spec.dtype).newbyteorder("<" if spec.byteorder == "little" else ">")
    arr = np.frombuffer(data, dtype=dt).astype(np.dtype(spec.dtype))
    shape = (spec.height, spec.width) if spec.channels == 1 else (spec.height, spec.width, spec.channels)
    return arr.reshape(shape)


def load_image_bytes(data: bytes, filename: str | None = None, raw_spec: RawSpec | None = None) -> np.ndarray:
    """Decode bytes into an (H, W) or (H, W, 3) numpy array."""
    if not data:
        raise ImageFormatError("empty upload")
    ext = _ext(filename)
    if ext in RAW_EXT or raw_spec is not None:
        if raw_spec is None:
            raise ImageFormatError("raw sensor data needs width, height and dtype")
        return load_raw(data, raw_spec)
    if ext in ARRAY_EXT or data[:6] == b"\x93NUMPY":
        try:
            arr = np.load(io.BytesIO(data), allow_pickle=False)
        except Exception as exc:  # noqa: BLE001
            raise ImageFormatError(f"invalid .npy file: {exc}") from exc
        if arr.ndim not in (2, 3):
            raise ImageFormatError(f".npy must be 2-D or 3-D, got shape {arr.shape}")
        return arr
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)  # multi-page TIFF -> first page
    except Exception as exc:  # noqa: BLE001
        raise ImageFormatError(f"unsupported or corrupt image ({filename or 'upload'}): {exc}") from exc
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        return np.array(img).astype(np.uint16 if img.mode.startswith("I;16") else np.int32)
    if img.mode == "F":
        return np.array(img, dtype=np.float32)
    if img.mode in ("L", "1"):
        return np.array(img.convert("L"))
    return np.array(img.convert("RGB"))


def load_image_path(path: str, raw_spec: RawSpec | None = None) -> np.ndarray:
    with open(path, "rb") as fh:
        return load_image_bytes(fh.read(), os.path.basename(path), raw_spec)


def is_supported(filename: str) -> bool:
    return _ext(filename) in SUPPORTED_EXT
