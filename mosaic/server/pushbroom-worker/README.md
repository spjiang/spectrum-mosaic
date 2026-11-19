# 推扫拼图 worker（pb_mosaic）

FigSpec 推扫高光谱（FS-60C）原始 DN 三件套 `figspec.hdr / figspec.spe / figspec.gps` → 直接地理编码的镶嵌立方体。
独立镜像，与框幅 worker 互不依赖；前端、后端与框幅共用（菜单「推扫拼图」）。

## 流程与成果

| 步骤 | 编号 | 内容 |
| --- | --- | --- |
| 核对三件套 | P0_check | 头文件、立方体大小、定位表逐项核对，问题直接给出原因 |
| 轨迹与时间 | P1_trajectory | 定位投到 UTM；行号 → 时间的线性时钟（见下文「时间戳」） |
| 切航带 | P2_strips | 航向稳定、速度正常的直线段，裁掉转弯和刹车 |
| 航带配准 | P3_align | 接缝带 NCC + 加权最小二乘，消除航带间 GPS 相对错位 |
| 重采样镶嵌 | P4_mosaic | 成果格网逐块反算原始行列，最近邻取 DN，按 BIL 写盘 |
| 写成果 | P5_products | ENVI 头（UTM map info）、RGB GeoTIFF 金字塔、预览、足迹 |
| 报告 | P6_report | report.json / report.md，含配准明细和处理假设 |

步骤编号与前端 `frontend/src/pages/pushbroom/labels.ts` 一致（有测试校验）。

输出目录（后端按任务建好，worker 只往里写）：

```
products/<架次>_mosaic.spe / .hdr     镶嵌立方体，原始 DN，uint16，0 = 无数据
products/<架次>_rgb.tif               RGB GeoTIFF（默认波段，原始 DN），带金字塔
products/<架次>_preview.png           2–98% 拉伸预览
products/<架次>_footprints.geojson    航带足迹（WGS84），含每条航带的配准修正量
process/trajectory.csv               逐条定位：时间、UTM、地速、航迹角、推算行号
report/report.json / report.md       处理报告
log/run.log                          运行日志；log/attempt-<n>.done 结束标记；log/control.json 取消标记
```

## 几何标定

只用 0414 架次标定，0419 / 0429 两架次核验。默认值在 `pb_mosaic/geom/sensor.py`，任务参数可逐项覆盖。

**时间戳。** 记录器约每 1.037 秒记一条：取当时的行计数（每 7 行更新一次）和「最近一次」GPS 定位。
定位本身整秒一个、时间准确，但到记录时已过去 0～1 秒，逐条配对会带来最多约 2.6 m 的锯齿误差
（dt ≈ 2.01 处就是滞后超过 1 秒、跳过一个定位）。相机行频恒定（约 69.99 Hz），所以对全部记录
拟合「行号 = 行频 × 时间」，取残差 2% 分位作为零滞后。残差跨度超过 1.5 秒（丢帧、中途暂停）时
退回逐条插值并在报告里提示。实现见 `geom/line_clock.py`。

**像元方向 `sample_sign = -1`。** 像元序号向航迹左侧增加。取 +1 时往返航带在重叠区出现约 ±26 m 的
旁向镜像错位，一眼可辨。厂商 MergeResult 看起来左右镜像且有偏移，不作为真值。

**沿航向偏移 `along_offset_m = 2.0`、狭缝中心 `boresight_px = -49`。** 由往返航带配准反推：
固定的时间滞后和安装偏差都折算成这两个量。不同架次之间仍有 1～2 m 的整体差异，由航带配准吸收。

**姿态不用。** gps 文件的 X/Y/ZVelocity 实为姿态角（ZVelocity 为偏航，度），加入后 0414 航带间错位
反而变大，判断相机在增稳云台上。偏航与航迹的差（偏流角）各架次约 1～2°，试过按偏流旋转狭缝，
闭合差只从 2.0 m 降到 1.6 m，不是主要因素，未采用。

**航带配准（`mosaic/align.py`）。** 非 RTK 的 GPS 在航带之间常差一两米。航带内还有随旁向位置变化的
残余形变（约 1 m 量级，整条平移消不掉）：隔一条的同向航带量到的平移与相邻接缝推出的值相差
0.7～2 m。因此：

- 看得见的接缝只在相邻航带中间（镶嵌取离狭缝中心最近的航带），只在接缝两侧各 4 m 内量平移。
  0419 架次相邻航带的相关性由全重叠区的 0.2～0.5 提高到 0.55～0.75。
- 不形成接缝的航带对（隔一条）只用全重叠区量，权重 ×0.05，仅在接缝链断开时帮忙连通。
- 带通（σ 1 / 10 像元）后做掩膜 NCC，每对保留 4 个候选峰；农田的周期纹理（旁向约 1.5 m、
  沿航向约 3.7 m）会有好几个差不多高的峰，按全局一致性迭代改选。
- 每个连通块内平移总和为零，整体仍以 GPS 为准。

| 架次 | 接缝 | 接缝相关性中位 | 最大修正 |
| --- | --- | --- | --- |
| 0414 | 4/4 | 0.67 | 0.28 m |
| 0419 | 4/4 | 0.63 | 1.67 m |
| 0429-134344 | 4/4 | 0.52 | 1.66 m |
| 0429-135339 | 单航带 | — | — |

0410 架次的三件套是 macOS 拷贝留下的 `._` 资源文件，核对阶段即拒绝。

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
| `MOSAIC_REQUIRE_LICENSE` | 空 | 为真时启动校验许可并定期复查（与框幅 worker 同一套） |
| `LICENSE_PATH` / `LICENSE_PUBLIC_KEY_PATH` / `LICENSE_SERVER_URL` / `HOST_MACHINE_ID_PATH` | | 同框幅 worker |
| `LICENSE_CLOCK_PATH` | `/data/.license/pushbroom-clock.json` | 防回拨时钟，与框幅分开 |
| `LICENSE_LEASE_PATH` | `/data/.license/pushbroom-lease.json` | 租约缓存，与框幅分开 |

## 本地开发

```bash
# 后端开关 + worker，叠加在原有 compose 上（不改原有文件）
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d --build backend pushbroom-worker

./run.sh test                      # 单元测试：合成数据 + 真实数据（缺失自动跳过）
./run.sh check <架次目录>
./run.sh run <架次目录> <输出目录> --bands 48,150,208
```

合成数据测试（`tests/synth.py`）按同一平地下视模型成像已知纹理，校验成果与真值逐像元一致、
配准能找回人为加入的航带 GPS 偏差。`scripts/` 下是标定时用的脚本，不进镜像。
