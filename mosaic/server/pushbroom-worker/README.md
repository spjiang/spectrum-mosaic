# 推扫拼图 worker（pb_mosaic）

FigSpec 推扫高光谱（FS-60C）原始 DN 三件套 `figspec.hdr / figspec.spe / figspec.gps` → 直接地理编码的镶嵌立方体。
独立镜像，与框幅 worker 互不依赖；前端、后端与框幅共用（菜单「推扫拼图」）。

## 流程与成果

| 步骤 | 编号 | 内容 |
| --- | --- | --- |
| 核对三件套 | P0_check | 头文件、立方体大小、定位表逐项核对，问题直接给出原因 |
| 轨迹与时间 | P1_trajectory | 定位投到 UTM；行号 → 时间的线性时钟（见下文「时间戳」） |
| 切航带 | P2_strips | 航向稳定、速度正常的直线段，裁掉转弯和刹车 |
| 航带配准 | P3_align | 整条平移（接缝带 NCC）→ 精配准（逐条狭缝剪切 + 平移细化）→ 传感器标定自检 |
| 重采样镶嵌 | P4_mosaic | 成果格网逐块反算原始行列，最近邻取 DN，按 BIL 写盘 |
| 写成果 | P5_products | ENVI 头（UTM map info）、原始 DN / 显示用 RGB GeoTIFF、预览、足迹 |
| 报告 | P6_report | report.json / report.md，含配准明细和处理假设 |

步骤编号与前端 `frontend/src/pages/pushbroom/labels.ts` 一致（有测试校验）。

输出目录（后端按任务建好，worker 只往里写）：

```
products/<架次>_mosaic.spe / .hdr     镶嵌立方体，原始 DN，uint16，0 = 无数据
products/<架次>_rgb.tif               RGB GeoTIFF（默认波段，原始 DN），带金字塔；GIS 默认打开会偏暗
products/<架次>_rgb_display.tif       显示用 8 位 RGB（1–98% + 伽马 0.8 + 通道增益），给人看、进 GIS
products/<架次>_preview.png           与显示 GeoTIFF 同一套拉伸的预览
products/<架次>_footprints.geojson    航带足迹（WGS84），含每条航带的配准平移和狭缝剪切
process/trajectory.csv               逐条定位：时间、UTM、地速、航迹角、推算行号
report/report.json / report.md       处理报告
log/run.log                          运行日志；log/attempt-<n>.done 结束标记；log/control.json 取消标记
```

## 几何标定

安装参数的取值顺序：任务参数 > 部署配置（环境变量 `PB_SENSOR_*`）> 内置 FS-60C 标定
（`pb_mosaic/geom/sensor.py`）。报告「传感器几何」一节写明每个参数取自哪里。

**时间戳。** 记录器约每 1.037 秒记一条：取当时的行计数（每 7 行更新一次）和「最近一次」GPS 定位。
定位本身整秒一个、时间准确，但到记录时已过去 0～1 秒，逐条配对会带来最多约 2.6 m 的锯齿误差
（dt ≈ 2.01 处就是滞后超过 1 秒、跳过一个定位）。相机行频恒定（约 69.99 Hz），所以对全部记录
拟合「行号 = 行频 × 时间」，取残差 2% 分位作为零滞后。残差跨度超过 1.5 秒（丢帧、中途暂停）时
退回逐条插值并在报告里提示。实现见 `geom/line_clock.py`。

**像元方向 `sample_sign = -1`。** 像元序号向航迹左侧增加。取 +1 时往返航带在重叠区出现约 ±26 m 的
旁向镜像错位，一眼可辨。厂商 MergeResult 看起来左右镜像且有偏移，不作为真值。

**沿航向偏移 `along_offset_m = 1.7`、狭缝中心 `boresight_px = -19`。** 固定的时间滞后和安装偏差折算成
这两个量，由往返航带的整体错开反推（`geom/calibration.py`）。内置值是 0419、0429-134344 两架次
`calibrate` 合并的结果（1.69 ± 0.11 m，−19.3 ± 3.0 像元）。0414 的狭缝中心约 −50 像元，比之后
两架次差 30 像元（地面 1.1 m），两架次之间互相一致而与 0414 不一致，判断 04-14 至 04-19 之间
重新安装过相机或云台；按内置值处理 0414 时，报告会给出标定不符的提示。

**狭缝剪切（`mosaic/refine.py`）。** 整条平移之后，往返相邻航带的重叠区里还有沿航向错位，且随旁向位置
线性变化（16 m 宽的重叠里从 −1 m 变到 +1 m，旁向错位都在 0.2 m 内）：狭缝与航迹不垂直，离中线
d 米的像元沿航向错开 φ·d 米。φ 往返翻转（偏流），同向航带之间也不完全一样，逐条估计，实测
±2.5°～±4.5°。按 gps 偏航列算的偏流只有 1～2°，而且是整数度，不用。所有航带共同的剪切从重叠区
量不出来，约定往返两个方向的平均剪切互为相反数（安装偏角按 0）。

**姿态不用。** gps 文件的 X/Y/ZVelocity 实为姿态角（整数度），加入俯仰、横滚后航带间错位反而变大，
判断相机在增稳云台上。

**航带配准。** 两步：

1. 整条平移（`mosaic/align.py`）：只在接缝两侧各 4 m 内量（看得见的接缝只在相邻航带中间），
   带通后掩膜 NCC，每对保留 4 个候选峰；农田的周期纹理（旁向约 1.5 m、沿航向约 3.7 m）会有好几个
   差不多高的峰，按全局一致性迭代改选。隔一条的同向航带只用来连通，权重 ×0.05。
2. 精配准（`mosaic/refine.py`）：全部重叠区按 2.4 m（旁向）× 20 m（沿航向）切块，块内 ±1.2 m 亚像元
   NCC，次峰太接近的块不用。Huber 加权最小二乘解每条航带的平移细化量和剪切；按新几何再量一轮
   （±0.5 m）细化，最后再量一轮作质检。修正后错位没变小，或剪切超过 8°、平移超过 1.5 m 时整体放弃，
   保留第 1 步的结果并在报告里提示。

每个连通块内平移总和为零，整体位置仍以 GPS 为准。600 波段全量验收（容器内存上限 12 GB，
`PB_MAX_MEMORY_GB=8`）：

| 架次 | 整条平移 | 精配准：块内错位中位（p90） | 狭缝剪切 | 标定自检 | 耗时 | 匿名内存峰值 | 成果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0414 | 4/4 接缝，最大 1.54 m | 0.54 → 0.16 m（1.05 → 0.26 m） | −2.8°～+3.9° | 狭缝中心 −50，提示不符 | 79 s | 2.0 GiB | 18.8 GiB |
| 0419 | 4/4 接缝，最大 0.34 m | 0.63 → 0.15 m（1.12 → 0.31 m） | −2.8°～+4.4° | 与内置一致 | 80 s | 1.8 GiB | 19.4 GiB |
| 0429-134344 | 4/4 接缝，最大 0.88 m | 0.75 → 0.18 m（1.15 → 0.31 m） | −3.7°～+3.1° | 与内置一致 | 75 s | 1.8 GiB | 19.0 GiB |
| 0429-135339 | 单航带 | — | — | 无法自检 | 12 s | 0.9 GiB | 6.3 GiB |

耗时在 12 核、NVMe 的开发机上测得，输入已在页缓存里；冷读 33 GB 时受磁盘速度限制。成果大小与
格网 × 波段 × 2 字节逐字节一致；抽 3 个波段与单独按这 3 个波段处理的结果逐像元相同。

0410 架次的三件套是 macOS 拷贝留下的 `._` 资源文件，核对阶段即拒绝。

### 重新标定（换相机、重新安装相机或云台之后）

1. 飞一个至少 3 条往返航带、相邻航带旁向重叠 30% 以上的架次，选纹理丰富的地块（农田、道路）。
2. `./run.sh calibrate <架次目录> [<架次目录> ...]`：只读不写，同一安装状态的架次越多越准。
   各架次之间明显不一致时不给合并值，并指出是哪一架。
3. 把输出里 `env` 的两行写进 worker 的环境变量（compose 里 `pushbroom-worker.environment`，或本地 `.env`），
   重启 worker。
4. 之后每个任务的报告都有「传感器标定自检」：本架次反推的建议值与当前设定相差超过 0.5 m、
   且超过 2 倍标准误差时，在提示和任务日志里告警。

## 队列协议

队列：`pushbroom.jobs`（任务）、`pushbroom.control`（取消）、`pushbroom.progress`、`pushbroom.events`、
`pushbroom.jobs.dlq`（无法解析的任务消息）。消息均带 `schema_version: 1`。

- 任务：`{job_id, run_attempt, input_dir, output_dir, params}`。输入、输出必须分别在
  `PB_INPUT_ROOTS`、`PB_OUTPUT_ROOTS` 之下（解析软链后判断），输出目录不能是根本身。
- 进度：`{stage_id, stage_progress 0–100, global_percent, message}`。
- 事件：`started` / `stage_done` / `log` / `succeeded {products}` / `failed {error}` / `cancelled`。
- 取消：`{job_id, run_attempt, action: "cancel"}`，后端同时写 `log/control.json`，消息丢了也能取消。

一次只跑一个任务。任务先 ack 再在子进程里跑，子进程独立进程组；忙时新消息不 ack 也不退回，
消费者一直在线。取消先 SIGTERM（在下一个检查点停下、删半截成果，通常 1 秒内），20 秒不退再 SIGKILL。
子进程被系统杀（多半是内存不足）、worker 被停或重启、许可复查失败时，都会补报失败并删除 `.part` 半截文件。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RABBITMQ_URL` | `amqp://mosaic:mosaic_secret@127.0.0.1:5672/` | |
| `PB_MAX_MEMORY_GB` | 8 | 算法内存预算，应小于容器内存上限 |
| `PB_INPUT_ROOTS` | `/data/input` | 允许的输入根，冒号分隔 |
| `PB_OUTPUT_ROOTS` | `/data/output` | 允许的输出根，冒号分隔 |
| `PUSHBROOM_WORKER_STATE_DIR` | `/data/.pushbroom-worker` | 心跳 status.json、current_job.json |
| `PB_SENSOR_ALONG_OFFSET_M` | 空（内置 1.7） | 本机标定：沿航向偏移，米。用 `calibrate` 得到 |
| `PB_SENSOR_BORESIGHT_PX` | 空（内置 −19） | 本机标定：狭缝中心相对正中的像元数 |
| `PB_SENSOR_SAMPLE_SIGN` | 空（内置 −1） | 像元方向，1 或 −1 |
| `PB_SENSOR_GSD_CROSS_M` / `PB_SENSOR_FLIGHT_HEIGHT_M` | 空 | 旁向分辨率 / 航高；不填按头文件估算 |
| `MOSAIC_REQUIRE_LICENSE` | 空 | 为真时启动校验许可并定期复查（与框幅 worker 同一套）；`pipeline.run`、`calibrate_flight` 入口也校验 |
| `LICENSE_PATH` / `LICENSE_PUBLIC_KEY_PATH` / `LICENSE_SERVER_URL` / `HOST_MACHINE_ID_PATH` | | 同框幅 worker |
| `LICENSE_CLOCK_PATH` | `/data/.license/pushbroom-clock.json` | 防回拨时钟，与框幅分开 |
| `LICENSE_LEASE_PATH` | `/data/.license/pushbroom-lease.json` | 租约缓存，与框幅分开 |

## 客户发版

发版台每次构建同时产出 `pushbroom-worker-image.tar`（`release/build/Dockerfile.pushbroom-customer`）：
算法模块按 `release/build/cython_modules_pushbroom.txt` 编成 `.so`，强制许可。**`pb_mosaic` 新增模块要加进这个清单**，
否则会以源码交付（发版台单测会拦下）。客户 compose 里推扫 worker 在 profile `pushbroom` 下，由 `.env` 的
`PUSHBROOM_ENABLED`、`COMPOSE_PROFILES` 开关；客户侧重新标定写 `.env` 的 `PB_SENSOR_*`，
步骤见 `docs/操作文档/拼图服务私有部署操作手册.md` 第 6 节。

## 本地开发

```bash
# 后端开关 + worker，叠加在原有 compose 上（不改原有文件）
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d --build backend pushbroom-worker

./run.sh test                      # 单元测试：合成数据 + 真实数据（缺失自动跳过）
./run.sh check <架次目录>
./run.sh run <架次目录> <输出目录> --bands 48,150,208
./run.sh calibrate <架次目录> [<架次目录> ...]
```

合成数据测试（`tests/synth.py`）按同一平地下视模型成像已知纹理，校验成果与真值逐像元一致、
配准能找回人为加入的航带 GPS 偏差和狭缝剪切、标定自检能找回人为设错的安装参数。
`scripts/` 下是与厂商成果比对的脚本，不进镜像。
