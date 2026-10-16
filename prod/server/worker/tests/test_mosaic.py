from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from ms_mosaic.mosaic import mosaic_paths


def test_mosaic_two_overlapping_tiles(tmp_path: Path):
    a = tmp_path / "a.tif"
    b = tmp_path / "b.tif"
    profile = {
        "driver": "GTiff",
        "height": 32,
        "width": 32,
        "count": 1,
        "dtype": "uint8",
        "crs": "EPSG:32647",
        "transform": from_origin(674000.0, 2620500.0, 0.5, 0.5),
    }
    with rasterio.open(a, "w", **profile) as dst:
        dst.write(np.full((1, 32, 32), 10, dtype=np.uint8))
    profile["transform"] = from_origin(674008.0, 2620500.0, 0.5, 0.5)
    with rasterio.open(b, "w", **profile) as dst:
        dst.write(np.full((1, 32, 32), 30, dtype=np.uint8))
    out = tmp_path / "mosaic.tif"
    meta = mosaic_paths([a, b], out)
    assert out.exists()
    with rasterio.open(out) as ds:
        assert ds.crs.to_string() == "EPSG:32647"
        assert ds.width > 32
        data = ds.read(1)
        assert data.max() >= 30
        assert (data > 0).sum() > 32 * 32
    assert meta["n_scenes"] == 2
