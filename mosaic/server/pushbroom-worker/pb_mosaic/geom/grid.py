"""成果格网：UTM、正北朝上、方形像元，左上角对齐到分辨率的整数倍。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OutputGrid:
    epsg: int
    x0: float  # 左边界
    y0: float  # 上边界
    gsd: float
    width: int
    height: int

    @property
    def zone(self) -> int:
        return self.epsg % 100

    @property
    def north(self) -> bool:
        return self.epsg // 100 == 326

    def col_x(self, cols: np.ndarray) -> np.ndarray:
        return self.x0 + (cols + 0.5) * self.gsd

    def row_y(self, rows: np.ndarray) -> np.ndarray:
        return self.y0 - (rows + 0.5) * self.gsd

    def to_colrow(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (x - self.x0) / self.gsd - 0.5, (self.y0 - y) / self.gsd - 0.5

    def affine(self) -> tuple[float, float, float, float, float, float]:
        """GDAL 顺序：x0, dx, 0, y0, 0, -dy"""
        return (self.x0, self.gsd, 0.0, self.y0, 0.0, -self.gsd)

    def as_dict(self) -> dict:
        return {
            "epsg": self.epsg,
            "x0": self.x0,
            "y0": self.y0,
            "gsd": self.gsd,
            "width": self.width,
            "height": self.height,
        }


def grid_from_bounds(epsg: int, xmin: float, ymin: float, xmax: float, ymax: float, gsd: float) -> OutputGrid:
    if gsd <= 0:
        raise ValueError("分辨率必须大于 0")
    x0 = math.floor(xmin / gsd) * gsd
    y0 = math.ceil(ymax / gsd) * gsd
    width = max(1, int(math.ceil((xmax - x0) / gsd - 1e-9)))
    height = max(1, int(math.ceil((y0 - ymin) / gsd - 1e-9)))
    return OutputGrid(epsg=epsg, x0=x0, y0=y0, gsd=gsd, width=width, height=height)


def default_gsd(cross_m: float, along_m: float) -> float:
    """取两向中较粗的一边，按 0.1 毫米取整，避免把任何一向放大。"""
    return round(max(cross_m, along_m), 4)
