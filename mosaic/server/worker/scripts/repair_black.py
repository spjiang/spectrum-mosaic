"""就地清已有成果的不透明黑，并按模版 edge_trim_m 收边。不套商业掩膜。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from ms_mosaic.products import GROUP_PREFIX, clean_product_directory


def black_stats(path: Path) -> str:
    with rasterio.open(path) as ds:
        rgb = ds.read()
    alpha = rgb[3] > 0 if rgb.shape[0] >= 4 else np.ones(rgb.shape[1:], bool)
    colored = (rgb[:3] > 0).any(axis=0)
    opaque = int(alpha.sum())
    black = int((alpha & ~colored).sum())
    return f"alpha={opaque} black={black} ({100 * black / max(opaque, 1):.2f}%)"


def main() -> None:
    dirs = [
        Path("/data/output/runs/demo_max_20251017_locked/拼图结果"),
        Path("/data/output/runs/demo_max_20251017_edgefix/拼图结果"),
    ]
    for folder in dirs:
        path = folder / f"{GROUP_PREFIX}0.tif"
        if not path.is_file():
            print("missing", path, flush=True)
            continue
        print("BEFORE", folder.parent.name, black_stats(path), flush=True)
        clean_product_directory(folder, trim_m=20.0)
        print("AFTER ", folder.parent.name, black_stats(path), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
