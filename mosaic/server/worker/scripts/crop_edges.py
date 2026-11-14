"""裁最新 restore 与商业的边缘窗口，供肉眼看接缝/油彩。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from scripts.crop_compare import REF_DIR, ORTHO, read_window
from rasterio.transform import Affine
import rasterio

OURS = Path("/data/output/runs/demo_max_20251017_restore/拼图结果") / ORTHO
OUT = Path("/data/output/_crop/edge_restore")

# 用户标过的右上坏边 + 四缘中点 + 内部对照
WINDOWS = [
    ("右上北", 674607.2, 2620470.2, 40.0),
    ("右上南", 674601.6, 2620222.0, 40.0),
    ("西缘", 673920.0, 2620230.0, 50.0),
    ("北缘", 674260.0, 2620540.0, 50.0),
    ("南缘", 674260.0, 2619920.0, 50.0),
    ("东缘", 674600.0, 2620230.0, 50.0),
    ("内部对照", 674250.0, 2620200.0, 40.0),
]


def save_pair(name: str, ours: np.ndarray, ref: np.ndarray, ok_o, ok_r, zoom: int = 2) -> None:
    o = np.where(ok_o[None], np.clip(ours, 0, 255), 0).astype(np.uint8)
    r = np.where(ok_r[None], np.clip(ref, 0, 255), 0).astype(np.uint8)
    gap = np.full((3, o.shape[1], 8), 255, np.uint8)
    pair = np.concatenate([o, gap, r], axis=2)
    img = np.moveaxis(pair, 0, -1)
    if zoom > 1:
        img = np.repeat(np.repeat(img, zoom, 0), zoom, 1)
    Image.fromarray(img).save(OUT / f"{name}.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with rasterio.open(REF_DIR / ORTHO) as ds:
        gsd = float(ds.transform.a)
        crs = str(ds.crs)
    for name, x, y, span in WINDOWS:
        n = max(8, int(round(span / gsd)))
        tr = Affine(gsd, 0, x - n * gsd / 2, 0, -gsd, y + n * gsd / 2)
        shape = (n, n)
        ours, oko = read_window(OURS, tr, crs, shape)
        ref, okr = read_window(REF_DIR / ORTHO, tr, crs, shape)
        save_pair(name, ours, ref, oko, okr)
        print(name, f"ours={int(oko.sum())} ref={int(okr.sum())}", flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
