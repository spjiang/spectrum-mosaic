"""核对重出正射：内部不应再有大块透明洞，也不该有不透明黑。"""

from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import binary_fill_holes, label

PATH = Path("/data/output/runs/demo_max_20251017_restore/拼图结果/Orthomosaic_pix_surf_group0.tif")


def main() -> None:
    with rasterio.open(PATH) as ds:
        rgb = ds.read()
    alpha = rgb[3] > 0
    colored = (rgb[:3] > 0).any(axis=0)
    opaque = int(alpha.sum())
    black = int((alpha & ~colored).sum())
    filled = binary_fill_holes(alpha)
    holes = filled & ~alpha
    n_hole, n_comp = int(holes.sum()), 0
    if holes.any():
        _, n_comp = label(holes)
    print(
        f"alpha={opaque} black={black} ({100 * black / max(opaque, 1):.2f}%) "
        f"interior_holes={n_hole} comps={n_comp}",
        flush=True,
    )


if __name__ == "__main__":
    main()
