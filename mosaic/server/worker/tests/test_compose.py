"""正射块粘接：重叠区必须羽化，不能在 384 格界上硬切。"""

import numpy as np

from ms_mosaic.compose import _feather_weight
from ms_mosaic.grid import Grid

CRS = "EPSG:32647"


def _grid(h=40, w=40, gsd=1.0):
    return Grid.from_bounds((0.0, 0.0, w * gsd, h * gsd), gsd, CRS)


def test_feather_weight_is_one_inside_whole_mosaic_edge():
    grid = _grid(20, 20)
    w = _feather_weight((0, 0, 12, 12), overlap=4, grid=grid)
    assert w[0, 0] == 1.0
    # 右/下是与下一块的重叠边，会降权；整幅的左/上边必须是 1
    assert w[1, 1] == 1.0
    assert float(w[0, 11]) < float(w[0, 0])


def test_feather_weight_crossfade_at_tile_boundary():
    """两块共享 2*overlap 宽的带，块界处权重大约各 0.5。"""
    grid = _grid(64, 64)
    overlap, tile = 4, 16
    a = _feather_weight((0, 0, 64, tile + overlap), overlap, grid)
    b0 = tile - overlap
    b = _feather_weight((0, b0, 64, tile + 2 * overlap), overlap, grid)
    seam = tile
    assert abs(float(a[8, seam]) - 0.5) < 0.08
    assert abs(float(b[8, seam - b0]) - 0.5) < 0.08
    assert float(a[8, tile - overlap]) > 0.9
    assert float(b[8, 0]) < 0.1
