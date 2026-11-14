"""核对用户标出的右上两点：收边后必须透明。"""

from __future__ import annotations

import rasterio
from rasterio.transform import rowcol

PTS = [(674607.2, 2620470.2), (674601.6, 2620222.0)]
PATHS = [
    "/data/output/runs/demo_max_20251017_locked/拼图结果/Orthomosaic_pix_surf_group0.tif",
    "/data/output/runs/demo_max_20251017_edgefix/拼图结果/Orthomosaic_pix_surf_group0.tif",
    "/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif",
]


def main() -> None:
    for path in PATHS:
        label = "商业" if "input" in path else path.split("/")[-3]
        with rasterio.open(path) as ds:
            print("==", label, ds.width, "x", ds.height, flush=True)
            for x, y in PTS:
                row, col = rowcol(ds.transform, x, y)
                if 0 <= row < ds.height and 0 <= col < ds.width:
                    pix = ds.read(window=((row, row + 1), (col, col + 1)))
                    print(f"  ({x},{y}) r={row} c={col} {pix.ravel().tolist()}", flush=True)
                else:
                    print(f"  ({x},{y}) 格网外 r={row} c={col}", flush=True)


if __name__ == "__main__":
    main()
