"""Defect localization on the die-level map.

For wafer maps, the failing dies *are* the defect pixels, so localization is
done by clustering failing dies (morphological closing + connected components)
rather than by a learned detector. Each region gets a bounding box, area,
centroid and a polygon outline; the full binary mask is also returned so it can
be exported as a segmentation mask. Regions are reported in the coordinate
frame of the original input image.
"""
from __future__ import annotations

import base64
import io

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LEVEL_COLORS = np.array([[40, 44, 52], [96, 170, 120], [230, 80, 60]], dtype=np.uint8)  # bg, pass, fail
SEVERITY_COLORS = {"Critical": (230, 57, 70), "Major": (244, 162, 97), "Minor": (233, 196, 106), "None": (120, 200, 140)}

# Patterns whose failing dies are spread across the whole wafer: one region = whole wafer.
WHOLE_WAFER = {"Random", "Near-full"}


def find_regions(levels: np.ndarray, label: str, min_area_frac: float = 0.004, max_regions: int = 8,
                 rel_to_largest: float = 0.2) -> tuple[list[dict], np.ndarray]:
    """Clusters of failing dies. Isolated background fails are ignored: a region must hold at least
    ``min_area_frac`` of the wafer and ``rel_to_largest`` of the dominant cluster's failing dies."""
    wafer = levels > 0
    fail = (levels == 2).astype(np.uint8)
    h, w = levels.shape
    wafer_area = max(int(wafer.sum()), 1)
    if label == "none" or fail.sum() == 0:
        return [], np.zeros_like(fail)
    if label in WHOLE_WAFER:
        ys, xs = np.nonzero(wafer)
        mask = fail
        return [{
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)],
            "area_px": int(fail.sum()), "area_frac": float(fail.sum() / wafer_area),
            "centroid": [float(xs.mean()), float(ys.mean())], "polygon": [], "kind": "distributed",
        }], mask
    k = max(3, int(round(min(h, w) / 40)) | 1)
    closed = cv2.morphologyEx(fail, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, cents = cv2.connectedComponentsWithStats(closed, 8)
    regions = []
    mask = np.zeros_like(fail)
    if n <= 1:
        return regions, mask
    fail_counts = np.bincount(lab[fail > 0], minlength=n)
    fail_counts[0] = 0
    order = np.argsort(fail_counts)[::-1]
    floor = max(2, min_area_frac * wafer_area, rel_to_largest * fail_counts[order[0]])
    for i in order[:max_regions]:
        fail_in = int(fail_counts[i])
        if fail_in < floor:
            break
        comp = (lab == i).astype(np.uint8)
        x, y, bw, bh = (int(v) for v in stats[i, :4])
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        poly = cv2.approxPolyDP(max(cnts, key=cv2.contourArea), 1.5, True).reshape(-1, 2).tolist() if cnts else []
        mask |= comp
        regions.append({"bbox": [x, y, bw, bh], "area_px": fail_in, "area_frac": float(fail_in / wafer_area),
                        "centroid": [float(cents[i][0]), float(cents[i][1])], "polygon": poly, "kind": "cluster"})
    return regions, mask


def render(levels: np.ndarray, regions: list[dict], label: str, severity: str, confidence: float,
           source: np.ndarray | None = None, min_side: int = 448) -> Image.Image:
    """Annotated image: the input (or the colourised die map) with boxes and outlines."""
    if source is not None and source.ndim == 3 and source.dtype == np.uint8:
        base = source[..., :3].copy()
    elif source is not None and source.ndim == 2:
        g = source.astype(np.float32)
        g = (255 * (g - g.min()) / max(float(g.max() - g.min()), 1e-6)).astype(np.uint8)
        base = np.stack([g] * 3, -1)
    else:
        base = LEVEL_COLORS[np.clip(levels, 0, 2)]
    h, w = base.shape[:2]
    scale = max(1.0, min_side / max(1, min(h, w)))
    img = Image.fromarray(base).resize((int(w * scale), int(h * scale)), Image.NEAREST)
    draw = ImageDraw.Draw(img)
    col = SEVERITY_COLORS.get(severity, (255, 255, 255))
    lw = max(2, int(scale))
    for r in regions:
        x, y, bw, bh = r["bbox"]
        draw.rectangle([x * scale, y * scale, (x + bw) * scale, (y + bh) * scale], outline=col, width=lw)
        if len(r.get("polygon", [])) >= 3:
            draw.line([(px * scale, py * scale) for px, py in r["polygon"] + [r["polygon"][0]]], fill=(255, 255, 255), width=1)
    text = f"{label}  {confidence * 100:.1f}%  {severity}"
    try:
        font = ImageFont.load_default(size=max(12, int(img.width / 28)))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    tb = draw.textbbox((0, 0), text, font=font)
    draw.rectangle([0, 0, tb[2] + 12, tb[3] + 10], fill=(0, 0, 0))
    draw.text((6, 4), text, fill=col, font=font)
    return img


def to_png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def mask_png_b64(mask: np.ndarray) -> str:
    return to_png_b64(Image.fromarray((mask > 0).astype(np.uint8) * 255))
