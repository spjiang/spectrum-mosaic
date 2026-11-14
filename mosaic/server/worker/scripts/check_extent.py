"""量自研 vs 商业有效覆盖，确认是不是收边收过头。"""

from pathlib import Path

import numpy as np
import rasterio

PATHS = {
    "restore": "/data/output/runs/demo_max_20251017_restore/拼图结果/Orthomosaic_pix_surf_group0.tif",
    "edgefix坏": "/data/output/runs/demo_max_20251017_edgefix/拼图结果/Orthomosaic_pix_surf_group0.tif",
    "商业": "/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif",
}


def main() -> None:
    stats = {}
    ref = None
    for name, path in PATHS.items():
        p = Path(path)
        if not p.is_file():
            print("missing", name, flush=True)
            continue
        with rasterio.open(p) as ds:
            alpha = ds.read(ds.count) > 0
            gsd = float(ds.transform.a)
        n = int(alpha.sum())
        stats[name] = (n, gsd, alpha)
        print(f"{name}: alpha={n} area={n * gsd * gsd / 1e4:.2f} ha", flush=True)
        if name == "商业":
            ref = alpha
    if ref is not None and "restore" in stats:
        ours = stats["restore"][2]
        extra = int((ours & ~ref).sum())
        miss = int((ref & ~ours).sum())
        both = int((ours & ref).sum())
        print(f"restore∩商业={both}  自研多={extra}  自研少={miss}", flush=True)
        print(f"restore/商业={stats['restore'][0] / max(stats['商业'][0], 1):.4f}", flush=True)


if __name__ == "__main__":
    main()
