# 多光谱拼图（MAX 目录 → 大图）

入口只有拍完后的那一个目录：

```text
MAX_20251017_001/ 里的 MAX_*.jpg / MAX_*.tif
        ↓
认文件、读 POS、按曝光捆帧、正射镶嵌
        ↓
mosaics/*.tif + report/quality.json
```

本机默认路径集中在 `local_defaults.sh` / `ms_mosaic/local_defaults.py`（解释器、测区、缓存、`runs/`）。

**输入任务目录只读。** 成果写到 `--output-dir`，默认在主程序目录下的 `runs/`。若输出落在输入目录里会直接报错退出。CLI 旗标与 Web 处理方案同名（`input_dir` → `--input-dir`）。

执行（先进入主程序安装目录）：

```bash
cd /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker

# 先跑 12 帧预览
./run.sh --max-frames 12 --bands Color

# 全目录，输出到 /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/manual_时间戳
./run.sh

# 指定输出目录
./run.sh --output-dir /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/manual_01
```

Python 默认 `/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/source/.venv/bin/python`，可用 `MS_MOSAIC_PYTHON` 覆盖。

当前几何是 **POS 直接地理定位 + 北向上重投影 + merge**，还没有空三。大图带 UTM 坐标，可丢进 QGIS。白板 `_W` 和 AGL≈0 的地面帧会自动丢掉。

流程图见 [执行流程.md](执行流程.md)。全量 / 只 RGB 的完整命令见 [命令行说明.md](命令行说明.md)。
