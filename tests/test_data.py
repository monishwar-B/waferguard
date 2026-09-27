import io
import os

import numpy as np
import pytest
from PIL import Image

from waferguard.data import dataset as ds
from waferguard.data import synthetic
from waferguard.data.io import ImageFormatError, RawSpec, is_supported, load_image_bytes, load_image_path, load_raw
from waferguard.data.preprocess import crop_to_wafer, dihedral, levels_to_tensor, preprocess, to_levels


def _encode(arr, fmt, **kw):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format=fmt, **kw)
    return buf.getvalue()


@pytest.fixture()
def rgb():
    return synthetic.to_rgb(synthetic.make("Center", 40, np.random.default_rng(0)))


@pytest.mark.parametrize("fmt,name", [("PNG", "a.png"), ("BMP", "a.bmp"), ("TIFF", "a.tif"), ("JPEG", "a.jpg")])
def test_raster_formats(rgb, fmt, name):
    arr = load_image_bytes(_encode(rgb, fmt), name)
    assert arr.shape == rgb.shape and arr.dtype == np.uint8


def test_tiff16_and_grayscale():
    g16 = (np.random.default_rng(0).random((32, 32)) * 60000).astype(np.uint16)
    buf = io.BytesIO()
    Image.fromarray(g16).save(buf, format="TIFF")
    arr = load_image_bytes(buf.getvalue(), "x.tiff")
    assert arr.dtype == np.uint16 and arr.shape == (32, 32)
    g8 = load_image_bytes(_encode((g16 // 256).astype(np.uint8), "PNG"), "g.png")
    assert g8.ndim == 2


def test_npy_and_raw():
    lv = synthetic.make("Donut", 30)
    buf = io.BytesIO()
    np.save(buf, lv)
    assert np.array_equal(load_image_bytes(buf.getvalue(), "m.npy"), lv)
    raw = (np.arange(12 * 10) % 255).astype(np.uint16)
    spec = RawSpec(width=12, height=10, dtype="uint16")
    out = load_image_bytes(raw.tobytes(), "frame.raw", spec)
    assert out.shape == (10, 12) and out.dtype == np.uint16
    big = RawSpec(4, 2, "uint8", byteorder="big")
    assert load_raw(bytes(8), big).shape == (2, 4)


def test_io_errors(tmp_path):
    with pytest.raises(ImageFormatError):
        load_image_bytes(b"", "x.png")
    with pytest.raises(ImageFormatError):
        load_image_bytes(b"not an image", "x.png")
    with pytest.raises(ImageFormatError):
        load_image_bytes(b"1234", "x.raw")
    with pytest.raises(ImageFormatError):
        load_raw(b"123", RawSpec(2, 2))
    buf = io.BytesIO()
    np.save(buf, np.zeros((2, 2, 2, 2)))
    with pytest.raises(ImageFormatError):
        load_image_bytes(buf.getvalue(), "x.npy")
    with pytest.raises(ImageFormatError):
        load_image_bytes(b"\x93NUMPYgarbage", "x.npy")
    p = tmp_path / "a.png"
    p.write_bytes(_encode(np.zeros((4, 4, 3), np.uint8), "PNG"))
    assert load_image_path(str(p)).shape == (4, 4, 3)
    assert is_supported("A.TIFF") and not is_supported("a.txt")


def test_to_levels_domains(rgb):
    lv, dom = to_levels(rgb)
    assert dom == "wafer_map_rgb" and set(np.unique(lv)) <= {0, 1, 2}
    raw = synthetic.make("Scratch", 30)
    lv2, dom2 = to_levels(raw)
    assert dom2 == "wafer_map_levels" and np.array_equal(lv2, raw)
    remapped, dom3 = to_levels(raw.astype(np.int32) * 100 + 7)
    assert dom3 == "wafer_map_levels" and np.array_equal(remapped, raw)
    gray_rgb = np.stack([raw * 100] * 3, -1).astype(np.uint8)
    assert to_levels(gray_rgb)[1] == "wafer_map_levels"
    # continuous-tone "camera" frame of a disc with a dark blob
    yy, xx = np.mgrid[0:120, 0:120]
    frame = np.where(np.hypot(xx - 60, yy - 60) < 50, 180, 20).astype(np.float32)
    frame += np.random.default_rng(0).normal(0, 2, frame.shape)
    frame[55:62, 40:47] = 60
    lv4, dom4 = to_levels(frame.astype(np.uint8))
    assert dom4 == "optical" and (lv4 == 2).sum() > 0 and (lv4 == 0).sum() > 0
    lv5, _ = to_levels(np.stack([frame] * 3, -1).astype(np.uint16))
    assert lv5.shape == frame.shape
    with pytest.raises(ValueError):
        to_levels(np.zeros((0,)))
    flat, _ = to_levels(np.full((20, 20), 7.5, np.float32))
    assert flat.shape == (20, 20)


def test_tensor_and_dihedral(rgb):
    t, lv, dom = preprocess(rgb, 32)
    assert t.shape == (32, 32, 3) and np.allclose(t.sum(-1), 1, atol=1e-5)
    assert crop_to_wafer(np.zeros((5, 5), np.uint8)).shape == (5, 5)
    assert levels_to_tensor(lv, 16).shape == (16, 16, 3)
    views = {dihedral(t, k).tobytes() for k in range(8)}
    assert len(views) >= 2 and np.array_equal(dihedral(dihedral(t, 1), 3), t)


def _write_dataset(root, names_with_split=True):
    for c in ("none", "Center"):
        os.makedirs(root / c)
        for i in range(10):
            split = ["train", "validation", "test"][i % 3] if names_with_split else None
            lv = synthetic.make(c, 32, np.random.default_rng(i))
            name = f"{split}_{c}_{i}.png" if split else f"{c}_{i}.png"
            Image.fromarray(synthetic.to_rgb(lv)).save(root / c / name)
    Image.fromarray(synthetic.to_rgb(synthetic.make("Center", 32, np.random.default_rng(0)))).save(
        root / "Center" / "dup.png")  # exact duplicate of Center_0
    (root / "Center" / "notes.txt").write_text("ignored")


def test_dataset_scan_split_and_cache(tmp_path):
    _write_dataset(tmp_path)
    idx = ds.scan(str(tmp_path))
    assert idx.classes == ["Center", "none"]
    s = ds.summary(idx)
    assert sum(s["splits"]["val"].values()) > 0
    ds.write_summary(idx, str(tmp_path / "s.json"))
    paths, y = idx.subset("train")
    x = ds.load_arrays(paths, 16, str(tmp_path / "cache"), progress=True)
    x2 = ds.load_arrays(paths, 16, str(tmp_path / "cache"))
    assert x.shape == (len(paths), 16, 16, 3) and np.array_equal(x, x2)
    xa = ds.load_arrays(idx.paths, 32)
    sp = ds.leakage_safe_split(xa, np.array(idx.labels), val_frac=0.2, test_frac=0.2)
    assert sp["report"]["exact_duplicates_removed"] >= 1
    allidx = np.concatenate([sp["train"], sp["val"], sp["test"]])
    assert len(allidx) == len(set(allidx.tolist()))
    assert ds.detect_split("real_validation_x_1.png") == "val" and ds.detect_split("foo.png") is None


def test_dataset_random_split_and_errors(tmp_path):
    _write_dataset(tmp_path, names_with_split=False)
    idx = ds.scan(str(tmp_path), classes=["none", "Center"])
    assert set(idx.splits) == {"train", "val", "test"}
    with pytest.raises(ValueError):
        ds.scan(str(tmp_path), classes=["missing"])
    with pytest.raises(FileNotFoundError):
        ds.scan(str(tmp_path / "nope"))
    (tmp_path / "empty" / "a").mkdir(parents=True)
    with pytest.raises(ValueError):
        ds.scan(str(tmp_path / "empty"))
