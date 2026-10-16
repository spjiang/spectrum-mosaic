# 多光谱拼图命令行使用说明

先进入**主程序安装目录**再执行命令。下文相对路径都相对该目录，与文件夹叫什么无关。

---

## 一、部署与安装

### 1.1 主程序目录

安装后工作目录就是这一层（名称不限），不要再往上找仓库布局：

```text
./
├── ms_mosaic/     # Python 包（入口 python -m ms_mosaic）
├── tests/
├── scripts/
├── docs/          # 本手册
├── runs/          # 运行输出（默认 --output-dir 落在这里）
├── run.sh
└── README.md
```

`--input-dir` 可以指向本目录以外的航摄数据。`--output-dir` 默认写到本目录的 `runs/`。  
**旗标名与 Web「处理方案」param key 一致**：`input_dir` → `--input-dir`，`dsm_gsd` → `--dsm-gsd`。旧名 `--input` / `--out` / `--workers` 已移除。

### 1.2 准备 Python 环境

需要能 `import rasterio, numpy, cv2` 的 Python 3。解释器路径按本机设置：

```bash
# 本机默认（run.sh 已写死，可用 MS_MOSAIC_PYTHON 覆盖）
PY="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/source/.venv/bin/python"
"$PY" -c "import rasterio, numpy, cv2; print('ok', rasterio.__version__)"
```

没有现成环境时，在主程序目录创建即可：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install rasterio numpy opencv-python tifffile Pillow scipy
export MS_MOSAIC_PYTHON="$(pwd)/.venv/bin/python"
```

`run.sh` 读取环境变量 `MS_MOSAIC_PYTHON`。

### 1.3 校验可运行

```bash
cd /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker
"$PY" -m ms_mosaic --help
./run.sh --help
```

### 1.4 读写约定（生产必守）

| 项 | 规则 |
| --- | --- |
| `--input-dir`（`input_dir`） | 拍完后的 `MAX_*` 任务目录，**只读** |
| `--output-dir`（`output_dir`） | 本次运行输出根；航摄原片不写回输入树，成果全部落在此目录 |
| 禁止 | `--output-dir` 落在 `--input-dir` 目录树内 |
| 默认行为 | 通用任务：UTM + 航高估 GSD + 足迹格网；**不会**因旁边有参考 `拼图结果/` 就自动锁格或套色 |

`--output-dir` 下各子目录分工如下（交付看 `拼图结果/`，出问题查 `log/`）：

```text
--output-dir/
  拼图结果/          正式交付：DSM 与各波段正射 GeoTIFF
  附件/              辅助查看：伪彩、测区 KML、拼接线
  cache/             中间缓存：特征点与空三，续跑可复用
  log/               运行日志与暂停/继续控制
  report/            质量报告附图与 JSON/Markdown
  质量报告.pdf
  比对报告.txt       仅设置了 --benchmark-dir 时出现
```

| 目录 / 文件 | 存放什么 | 起什么作用 |
| --- | --- | --- |
| `拼图结果/` | `DSM.tif`；`Orthomosaic_pix_surf_group0.tif`（RGB 正射）；`group1`～`group7`（450–850 nm 多光谱正射） | **主交付**。坐标系、GSD、覆盖以这里为准，下载与验收都看这一层 |
| `附件/` | `DSM_pseudocolor.tif`（高程伪彩）；`area.kml`（测区范围，可在地球软件打开）；`seamlines.geojson`（正射拼接线） | 给人看缝、看范围、看高程起伏，一般不当主成果 |
| `cache/` | `features/`（每张影像的 RootSIFT 特征）；`at_result.npz`（空三：相机、姿态、稀疏点） | 中途失败或只重做正射时不必从提特征重跑。清空则空三重做 |
| `log/` | `run.log`（文本日志，界面可拉进度）；`run.json`（质量统计副本）；`control.json`（暂停/继续/取消） | 查这次怎么跑的、任务卡在哪 |
| `report/` | `quality.json`、`quality.md`、`figures/` | 质量报告的数据和插图源 |
| `质量报告.pdf` | 空三精度、覆盖、重叠等汇总 | 给人阅读的报告 |
| `比对报告.txt` | 与商业 `拼图结果/` 的 CRS、GSD、格网、覆盖对照 | 只当尺子，**不改出图** |

---

## 二、命令行参数说明

入口（**参数名 = Web 处理方案 key**，写法为 `--` + 下划线改短横线）：

```bash
"$PY" -m ms_mosaic --input-dir <MAX目录> --output-dir <输出根> [选项...]
# 或
./run.sh [选项...]     # 未写 --output-dir 时落到 runs/manual_时间戳
```

阶段顺序：`S0_io → S1_catalog → S2_at → S3_dense → S4_dsm → S5_ortho → S6_report`。  
布尔项支持 `--foo` / `--no-foo`（如 `--drop-white-panel` / `--no-drop-white-panel`）。  
语义细节见同目录 **参数说明**（`参数说明.md`）；下表只列 CLI ↔ Web 对照。

### 1 输入输出（S0_io · Input/Output）

| CLI | Web key |
| --- | --- |
| `--input-dir` | `input_dir` |
| `--output-dir` | `output_dir` |
| `--cache-dir` | `cache_dir` |
| `--log-dir` | `log_dir` |
| `--process-dir` | `process_dir` |
| `--products-dir-name` | `products_dir_name` |
| `--run-mode` | `run_mode` |
| `--start-stage` | `start_stage` |
| `--stop-after-stage` | `stop_after_stage` |
| `--preset` | `preset`（`rgb_preview` 强制 `bands=Color`） |
| `--benchmark-dir` | `benchmark_dir` |
| `--match-reference-color` / `--no-match-reference-color` | `match_reference_color` |
| `--memory-gb` | `memory_gb` |
| `--cpus` | `cpus` |

### 2 影像编目（S1_catalog · Catalog）

| CLI | Web key |
| --- | --- |
| `--max-index` | `max_index` |
| `--max-frames` | `max_frames` |
| `--min-agl-m` | `min_agl_m` |
| `--max-tilt-deg` | `max_tilt_deg` |
| `--drop-white-panel` / `--no-drop-white-panel` | `drop_white_panel` |
| `--require-pos` / `--no-require-pos` | `require_pos` |

### 3 空三解算（S2_at · Aerial Triangulation）

| CLI | Web key |
| --- | --- |
| `--primary-band` | `primary_band`（当前仅 `Color`） |
| `--workers-at` | `workers_at` |
| `--sigma-xy-m` | `sigma_xy_m` |
| `--sigma-z-m` | `sigma_z_m` |
| `--sigma-attitude-deg` | `sigma_attitude_deg` |
| `--outlier-threshold-px` | `outlier_threshold_px` |
| `--calibrate-intrinsics` / `--no-calibrate-intrinsics` | `calibrate_intrinsics` |

### 4 密集匹配（S3_dense · Dense Matching）

| CLI | Web key |
| --- | --- |
| `--dsm-gsd` | `dsm_gsd` |
| `--reuse-dsm` | `reuse_dsm` |
| `--n-layers` | `n_layers` |
| `--z-margin-m` | `z_margin_m` |
| `--workers-dense` | `workers_dense` |

### 5 DSM 生成（S4_dsm · Digital Surface Model）

| CLI | Web key |
| --- | --- |
| `--spike-tolerance-m` | `spike_tolerance_m` |
| `--max-fill-gap-m` | `max_fill_gap_m` |
| `--grid-reference` | `grid_reference` |
| `--terrain-margin-lo-m` | `terrain_margin_lo_m` |
| `--terrain-margin-hi-m` | `terrain_margin_hi_m` |
| `--terrain-min-half-span-m` | `terrain_min_half_span_m` |

### 6 正射镶嵌（S5_ortho · Orthomosaic）

| CLI | Web key |
| --- | --- |
| `--bands` | `bands`（逗号分隔；Web 为字符串数组） |
| `--color-correction` | `color_correction` |
| `--seamline-enabled` / `--no-seamline-enabled` | `seamline_enabled` |
| `--workers-ortho` | `workers_ortho` |
| `--edge-trim-m` | `edge_trim_m` |
| `--flatten-edge-win-m` | `flatten_edge_win_m` |
| `--flatten-edge-band-m` | `flatten_edge_band_m` |
| `--radiometric-normalize` / `--no-radiometric-normalize` | `radiometric_normalize`（需同时填 `benchmark_dir`） |

### 7 质量报告（S6_report · Quality Report）

| CLI | Web key |
| --- | --- |
| `--write-pdf-report` / `--no-write-pdf-report` | `write_pdf_report` |
| `--write-json-report` / `--no-write-json-report` | `write_json_report` |

### 8 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 10 | 阶段完成，等待继续（`awaiting_continue`） |
| 2 | 失败 |
| 130 | 取消 |

### 9 成果布局

相对 `--output-dir`。完整说明见 **1.4**。

| 路径 | 内容 |
| --- | --- |
| `拼图结果/DSM.tif` | 数字表面模型（高程） |
| `拼图结果/Orthomosaic_pix_surf_group0.tif` | RGB 正射（含透明通道） |
| `拼图结果/Orthomosaic_pix_surf_group1.tif` … `group7.tif` | 全量时的 7 个多光谱正射 |
| `附件/DSM_pseudocolor.tif` | DSM 伪彩色 |
| `附件/area.kml` | 测区范围 |
| `附件/seamlines.geojson` | 拼接线 |
| `质量报告.pdf` | 自研质量报告 |
| `比对报告.txt` | 仅设置了 `--benchmark-dir` 时生成 |

---

## 三、本机测区示例（MAX_20251017）

下面都是这台机器上的绝对路径，进主程序目录后可直接复制执行。

```bash
cd "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker"

PY="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/source/.venv/bin/python"
INPUT="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/MAX_20251017_001"
BENCH="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/拼图结果"
CACHE="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_MAX_20251017_001/cache/features"
DSM="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_surface_rgb/拼图结果/DSM.tif"
```

```bash
test -d "$INPUT" && echo "测区数据 OK"
```

> 正在跑的任务不要再往同一个 `--output-dir` 写。

### 3.1 本测区出图

```bash
"$PY" -u -m ms_mosaic \
  --input-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/MAX_20251017_001" \
  --output-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb" \
  --bands Color \
  --cache-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_MAX_20251017_001/cache/features" \
  --workers-at 12 --workers-dense 12 --workers-ortho 12
```

要顺便对照商业尺子、写出 `比对报告.txt`，**多加一行即可**，出图不变：

```bash
  --benchmark-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/拼图结果"
```

不要加 `--match-reference-color`。那是把商业颜色抄过来，调算法时开了等于作弊。

### 3.2 复用已有 DSM 只重做正射

```bash
"$PY" -u -m ms_mosaic \
  --input-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/MAX_20251017_001" \
  --output-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_smooth" \
  --bands Color \
  --reuse-dsm "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_surface_rgb/拼图结果/DSM.tif" \
  --cache-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_MAX_20251017_001/cache/features" \
  --workers-ortho 12
```

### 3.3 跑全量（RGB + 7 多光谱）

```bash
"$PY" -u -m ms_mosaic \
  --input-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/MAX_20251017_001" \
  --output-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/full_surface_all" \
  --cache-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_MAX_20251017_001/cache/features" \
  --workers-at 12 --workers-dense 12 --workers-ortho 12
```

### 3.4 阶段调试 / 少帧预览

```bash
"$PY" -u -m ms_mosaic \
  --input-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/MAX_20251017_001" \
  --output-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/dbg_until_dsm" \
  --bands Color \
  --run-mode until_stage \
  --stop-after-stage S4_dsm

./run.sh --max-frames 12 --bands Color \
  --output-dir "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/preview_rgb"
```

### 3.5 如何确认跑完

```bash
pgrep -lf ms_mosaic
ls -lh "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb/拼图结果"
ls "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb/比对报告.txt"
grep 未达标 "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb/比对报告.txt"
```

进程已退出且 `拼图结果/` 已写出，才算本趟结束。
