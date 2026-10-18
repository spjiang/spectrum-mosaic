import numpy as np
from rasterio.transform import Affine

from ms_mosaic.grid import Grid
from ms_mosaic.seam_edit import rasterize_window


def test_rasterize_window_burns_only_the_painted_cells():
    grid = Grid(Affine(1, 0, 0, 0, -1, 20), 20, 20, "EPSG:32650")
    xy = np.array([[2.0, 18.0], [6.0, 18.0], [6.0, 14.0], [2.0, 14.0]])
    burned = rasterize_window(grid, (0, 0, 20, 20), [(xy, 9)])
    assert burned is not None
    assert burned[4, 4] == 9
    assert burned[0, 0] == -1
