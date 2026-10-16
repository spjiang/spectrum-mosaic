from pathlib import Path

import numpy as np
import pytest
import rasterio

from ms_mosaic.local_defaults import INPUT as REAL
from ms_mosaic.pipeline import run_mosaic


def test_refuses_to_write_into_input_dir(tmp_path: Path):
    inp = tmp_path / "MAX_20251017_001"
    inp.mkdir()
    with pytest.raises(ValueError, match="禁止写入输入目录"):
        run_mosaic(inp, inp)
    with pytest.raises(ValueError, match="禁止写入输入目录"):
        run_mosaic(inp, inp / "mosaics")


def test_payload_lists_commercial_product_names():
    """交付清单必须用商业成品的文件名，而不是早期的 rgb.tif / ms_550nm.tif。"""
    from ms_mosaic.products import (
        GROUP_PREFIX,
        MS_BANDS,
        PRODUCTS_DIRNAME,
        REPORT_PDF_NAME,
        RGB_BAND,
        group_name,
    )

    assert group_name(RGB_BAND) == f"{GROUP_PREFIX}0.tif"
    assert group_name("550nm") == f"{GROUP_PREFIX}2.tif"
    assert len(MS_BANDS) == 7
    assert PRODUCTS_DIRNAME == "拼图结果"
    assert REPORT_PDF_NAME == "质量报告.pdf"


@pytest.mark.slow
@pytest.mark.skipif(not REAL.exists(), reason="no MAX_20251017_001 dataset")
def test_run_mosaic_writes_dsm_and_rgba_ortho(tmp_path: Path):
    before = {p.name for p in REAL.iterdir()}
    result = run_mosaic(
        REAL,
        tmp_path,
        max_frames=8,
        max_index=20,
        dsm_gsd=0.4,
        workers=1,
        bands=("Color",),
    )
    after = {p.name for p in REAL.iterdir()}
    assert after == before
    rgb = Path(result["files"]["rgb"])
    assert rgb.exists() and rgb.name == "Orthomosaic_pix_surf_group0.tif"
    assert rgb.parent.name == "拼图结果"
    assert REAL not in rgb.resolve().parents
    with rasterio.open(rgb) as ds:
        assert ds.crs.to_string() == "EPSG:32647"
        assert ds.count == 4
        assert ds.dtypes[0] == "uint8"
        assert ds.width > 50 and ds.height > 50
        assert int(ds.read(4).max()) == 255
    dsm = Path(result["files"]["dsm"])
    assert dsm.exists()
    with rasterio.open(dsm) as ds:
        assert ds.count == 1 and ds.dtypes[0] == "float32"
        arr = ds.read(1, masked=True)
        assert arr.count() > 100
    pdf = Path(result["files"]["report_pdf"])
    assert pdf.exists() and pdf.read_bytes().startswith(b"%PDF")
    assert pdf.name == "质量报告.pdf"
    assert Path(result["files"]["kml"]).exists()
    assert Path(result["files"]["pseudocolor"]).exists()
