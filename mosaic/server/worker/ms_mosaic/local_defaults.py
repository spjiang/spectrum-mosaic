"""本机开发默认路径（算法热路径不读这里）。

换机器时改这一处，或用环境变量覆盖：
MS_MOSAIC_PYTHON / MS_MOSAIC_INPUT / MS_MOSAIC_BENCHMARK /
MS_MOSAIC_CACHE / MS_MOSAIC_REUSE_DSM / MS_MOSAIC_RUNS
"""

from __future__ import annotations

import os
from pathlib import Path

HOST_ROOT = Path("/Users/jiangshengping/wwwroot/shenzhen/spectrum")
PROD = HOST_ROOT / "algorithm" / "projects" / "数据融合" / "prod"
PROGRAM = PROD / "server" / "worker"
DATA = PROD / "server" / "data"

PYTHON = Path(
    os.environ.get(
        "MS_MOSAIC_PYTHON",
        str(HOST_ROOT / "algorithm" / "source" / ".venv" / "bin" / "python"),
    )
)
INPUT = Path(
    os.environ.get(
        "MS_MOSAIC_INPUT",
        str(DATA / "input" / "MAX_20251017" / "MAX_20251017_001"),
    )
)
BENCHMARK = Path(
    os.environ.get(
        "MS_MOSAIC_BENCHMARK",
        str(PROD / "docs" / "需求" / "测试正式数据" / "MAX_20251017" / "拼图结果"),
    )
)
CACHE = Path(
    os.environ.get(
        "MS_MOSAIC_CACHE",
        str(PROD / "runs" / "full_MAX_20251017_001" / "cache" / "features"),
    )
)
REUSE_DSM = Path(
    os.environ.get(
        "MS_MOSAIC_REUSE_DSM",
        str(PROD / "runs" / "full_surface_rgb" / "拼图结果" / "DSM.tif"),
    )
)
RUNS = Path(os.environ.get("MS_MOSAIC_RUNS", str(DATA / "output" / "runs")))

available = INPUT.is_dir()
