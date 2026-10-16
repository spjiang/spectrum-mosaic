# 多光谱拼图可视化管控台（visual_server）设计规格

> 状态：待实现  
> 日期：2026-09-18  
> 范围：`prod/visual_server` + `prod/source` 最小钩子（宿主机 worker）  
> 性质：**生产交付**，非演示

---

## 1. 目标与非目标

### 1.1 目标

1. 按**管线阶段**组织全部可配置参数；配置员能看清「参数属于哪一阶段、起什么作用」。
2. 参数与执行记录落 **PostgreSQL**；执行时写入**不可变参数快照**，便于溯源。
3. 执行过程可视化：当前阶段、总进度条、ETA；支持暂停（阶段边界）、续跑、取消。
4. **阶段运行控制**：可只跑到指定阶段停下验收；验收通过后手动「继续下一阶段」；某阶段有问题则不必往下跑，节约时间。
5. **快速预览**：可配置仅拼 RGB（`Color`），跳过 7 个多光谱正射，尽快看到几何结果。
6. React 前端 + FastAPI 后端，目录独立于 `source`，路径为 `prod/visual_server/`；另有**命令行教学页**，不部署可视化也能按文档/CLI 跑通。
7. `docker-compose` 部署：PostgreSQL、RabbitMQ、backend、frontend（**不含**计算容器）。
8. 计算仍在宿主机：`ms_mosaic` CLI 保持兼容；页面可点击下发同一套任务。
9. 通过 **RabbitMQ** 解耦：API 不跑重计算；worker 消费任务并回传进度。

### 1.2 非目标（本期不做）

- 将 `source` 打进 Docker / 与 backend 同容器跑拼图。
- 多任务并行计算（全局同时仅 1 个 `running`）。
- 影像上传到平台对象存储（采用宿主机绝对路径 + volume 挂载校验）。
- SSO/LDAP（本期本地账号 + 角色；预留扩展）。
- 阶段内「硬暂停」到任意指令周期（仅阶段边界暂停；UI 明示）。

---

## 2. 已确认决策

| 项 | 选择 |
| --- | --- |
| 交付档位 | 生产级完整闭环（配置 + 执行 + 溯源 + 报告） |
| 账号 | 多角色：配置员 / 执行员 / 只读 / 管理员 + 审计 |
| 并发 | 单任务串行 + 排队 |
| 数据路径 | 宿主机绝对路径；compose 挂载数据根供 backend 校验与读日志/报告 |
| 干预 | 取消 + 暂停/续跑（阶段检查点）+ **阶段性运行控制** |
| 快速预览 | 支持「仅 RGB」波段集；可与阶段性运行组合 |
| 计算部署 | 宿主机 worker；compose 不含 worker 镜像 |
| CLI | `python -m ms_mosaic` 行为保持；可选接入同一上报通道；前端提供命令行教学页 |

---

## 3. 目录与部署拓扑

```text
algorithm/projects/数据融合/prod/
├── source/                          # 主程序（宿主机执行，不进 compose）
│   └── ms_mosaic/
│       ├── …现有模块…
│       ├── progress.py              # 新增：ProgressReporter 抽象（默认 no-op）
│       ├── control.py               # 新增：cancel/pause 标志轮询
│       └── worker_main.py           # 新增：MQ 消费者入口
├── visual_server/                   # 新建，与 source 同级
│   ├── docker-compose.yml           # postgres + rabbitmq + backend + frontend
│   ├── .env.example
│   ├── README.md
│   ├── db/migrations/               # 版本化 SQL
│   ├── backend/                     # FastAPI
│   └── frontend/                    # React (Vite)
├── docs/specs/                      # 本规格
└── runs/ …                          # 既有输出根（示例）
```

### 3.1 运行关系

```text
浏览器 → frontend → backend(API + WS)
                      ├─ PostgreSQL
                      └─ RabbitMQ ──jobs/control──► 宿主机 ms_mosaic_worker
                                      ◄─progress/events──
                                                    │
                                                    ▼
                                              run_mosaic(...)
```

### 3.2 Compose 服务

| 服务 | 说明 |
| --- | --- |
| `postgres` | 参数定义、模板、任务、用户、审计 |
| `rabbitmq` | 任务/控制/进度/事件 |
| `backend` | FastAPI；挂载 `DATA_ROOT`（只读或读写策略见 §8） |
| `frontend` | nginx 托管静态资源并反代 `/api`、`/ws` |

宿主机另需：`systemd` 或 `scripts/run_worker.sh` 启动 `python -m ms_mosaic.worker_main`，环境变量指向 RabbitMQ（`host.docker.internal` 或宿主机网络 IP）。

---

## 4. 管线阶段

| stage_id | 名称 | 检查点产物（续跑起点） |
| --- | --- | --- |
| `S0_io` | 数据与目录 | 路径校验通过即可 |
| `S1_catalog` | 扫描与过滤 | `cache/catalog_shots.json`（可选） |
| `S2_at` | 空三（稀疏重建） | `cache/features/` + `cache/at_result.npz`（约定名以实现为准） |
| `S3_dense` | 密集匹配 | `cache/dense/` 分块或完整 height field |
| `S4_dsm` | DSM 生成 | `{out}/拼图结果/DSM.tif` |
| `S5_ortho` | 正射、拼接线、融合 | 分波段中间/成品 GeoTIFF |
| `S6_report` | 质量报告与归档 | `{out}/拼图结果/质量报告.pdf` + 日志目录 |

页面执行台用步骤条映射上述 stage；进度上报必须带 `stage_id`。

### 4.1 阶段性运行控制（生产调试核心）

目的：上阶段有问题就别跑后面；确认无误再手动继续，省时间。

| 运行模式 `run_mode` | 行为 |
| --- | --- |
| `full` | 从 `start_stage`（默认 S0）一直跑到 S6（或到失败/取消） |
| `until_stage` | 从 `start_stage` 跑到 `stop_after_stage`（含）后进入 **`awaiting_continue`**，不自动进入下一阶段 |
| `step` | 每完成**一个**阶段就进入 `awaiting_continue`，等同于「一步一确认」 |

控制动作（执行台按钮）：

| 动作 | 语义 |
| --- | --- |
| 启动 | 按 `run_mode` / `start_stage` / `stop_after_stage` 入队 |
| 继续下一阶段 | 仅当状态为 `awaiting_continue`：把下一阶段作为新的一段任务投递（复用同一 `job_id`、同一 `output_dir` 与检查点） |
| 跑到某某阶段 | 在等待态修改本次的 `stop_after_stage` 后继续（可选快捷） |
| 暂停 / 取消 | 同原设计 |

规则：

1. 阶段失败 → `failed`，**不**自动进入后续阶段。  
2. `awaiting_continue` 不算占用「全局 running」槽，下一名排队任务可以开始（仍保证同时最多一个 `running`）。若业务希望调试任务独占队列，可由设置 `hold_queue_while_awaiting`（默认 false）控制。  
3. 「继续」必须校验检查点齐全；缺失则拒绝并提示重跑该阶段。  
4. CLI 同等能力：`--start-stage`、`--stop-after-stage`、`--run-mode step|until_stage|full`。

### 4.2 仅 RGB 快速看图

| 配置 | 效果 |
| --- | --- |
| 预设「仅 RGB」或 `bands=["Color"]` | S5 只正射 RGB；不生成 group1–7；空三仍用主波段 Color（与现逻辑一致） |
| 可与 `until_stage=S5_ortho` 组合 | 更快得到可在 QGIS 打开的 `Orthomosaic_pix_surf_group0.tif` |
| `reuse_dsm` + 仅 RGB | 跳过密集，进一步加速预览 |

前端参数区提供一键预设：**仅 RGB 快速预览**（写入 bands + 可选推荐 `max_frames` 提示，不强制改帧数）。

---

## 5. 参数模型与详细字典

### 5.1 存储模型

- `param_definitions`：系统参数字典（key、阶段、类型、默认、校验、详细说明、advanced 标志、排序）。随迁移种子数据更新。
- `param_profiles`：命名模板（名称、描述、版本、更新人、时间）。
- `param_profile_values`：`(profile_id, param_key, value_json)`。
- 执行时复制整包 → `job_runs.params_snapshot`（JSONB，不可变）。

前端：按 `stage_id` 分组；每项展示说明；高级参数默认折叠。

### 5.2 参数字典（生产配置面）

说明列面向配置员；`cli` 列表示是否已有 CLI/代码入口（无则本期先入库展示，worker 映射到 kwargs/环境或后续接通）。

#### S0_io — 数据与目录

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `input_dir` | path | 必填 | 输入 `MAX_*` 目录（宿主机绝对路径）。只读扫描，禁止把输出写进此树。 |
| `output_dir` | path | 必填 | 本次运行输出根目录。其下生成 `拼图结果/`、`附件/`、`cache/`、`log/` 等。 |
| `cache_dir` | path | `{output_dir}/cache/features` | 特征/空三缓存。续跑可复用，避免重复提特征。 |
| `log_dir` | path | `{output_dir}/log` | 明码文本日志目录；执行记录存此路径便于溯源打开。 |
| `process_dir` | path | `{output_dir}/附件` | 中间过程图/调试产物根（与 `拼图结果/` 分离；对应代码中的 extras）。 |
| `products_dir_name` | string | `拼图结果` | 商业对齐的成果子目录名。 |
| `path_must_under_data_root` | bool | true | 输入/输出必须落在管理员配置的 `DATA_ROOT` 白名单下。 |
| `run_mode` | enum | `full` | `full` / `until_stage` / `step`。阶段性调试见 §4.1。 |
| `start_stage` | enum | `S0_io` | 从哪一阶段开始（须有检查点或从 S0）。 |
| `stop_after_stage` | enum\|null | null | `until_stage` 时必填：跑完该阶段后等待人工继续；`full` 时忽略。 |
| `preset` | enum\|null | null | 快捷预设：`rgb_preview`（仅 Color）等；保存模板时可展开为具体键值。 |
| `benchmark_dir` | path\|null | null | 调试对照 `拼图结果`：只写比对报告，不改格网/覆盖/颜色。 |
| `match_reference_color` | bool | false | 调试套色。合格主路径关闭。 |

#### S1_catalog — 扫描与过滤

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `max_index` | int\|null | null | 只扫描文件名编号 ≤ 该值的曝光，便于小范围调试。 |
| `max_frames` | int\|null | null | 过滤后最多使用前 N 个可用曝光。 |
| `min_agl_m` | float | 5.0 | 相对航高低于此值视为地面/无效帧丢弃。 |
| `max_tilt_deg` | float | 60.0 | 光轴偏离天底超过此角度丢弃（REQ-03-03）。 |
| `drop_white_panel` | bool | true | 丢弃文件名角色 `_W` 白板帧（不参与重建）。 |
| `require_pos` | bool | true | 无 XMP/EXIF POS 的帧丢弃；若未来支持无 POS 模式可关。 |

#### S2_at — 空三

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `primary_band` | enum | `Color` | 主波段：仅此波段做特征匹配与空三；其余波段迁移外方位。 |
| `workers_at` | int\|null | 10 | 特征提取等并行度。 |
| `sigma_xy_m` | float | 3.0 | GNSS 平面先验标准差（米），来自 XMP 精度量级。 |
| `sigma_z_m` | float | 5.0 | GNSS 高程先验标准差（米）。 |
| `sigma_attitude_deg` | float | 3.0 | IMU 姿态先验标准差（度）。 |
| `outlier_threshold_px` | float | 6.0 | 重投影外点阈值（像素）。 |
| `huber_scale_px` | float | 1.5 | 稳健核尺度，抑制粗差拉偏。 |
| `min_triangulation_angle_deg` | float | 1.0 | 最小交会角；过小的点不稳定。 |
| `min_obs_per_track` | int | 2 | 轨迹最少观测数。 |
| `pair_max_neighbors` | int | 12 | 每影像最多匹配邻域数（POS 足迹筛选）。 |
| `pair_min_overlap` | float | 0.45 | 足迹重叠下限才配对。 |
| `match_ratio_test` | float | 0.80 | Lowe 比值测试。 |
| `match_ransac_threshold_px` | float | 2.0 | 本质矩阵 RANSAC 阈值。 |
| `match_min_inliers` | int | 20 | 最少内点数。 |
| `calibrate_intrinsics` | bool | true | 是否分阶段释放内参自标定。 |

#### S3_dense — 密集匹配

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `dsm_gsd` | float\|null | null | DSM 地面分辨率（米）。空则按航高/焦距估计，正射为其一半。 |
| `reuse_dsm` | path\|null | null | 若指定，跳过密集匹配，复用已有 `DSM.tif`（可补覆盖）。 |
| `n_layers` | int | 48 | 沿高程扫描层数；越大越慢越细。 |
| `z_margin_m` | float\|null | null | 高程搜索半宽。空则按稀疏点起伏估计。 |
| `ncc_window` | int | 7 | NCC 窗口（像素）。 |
| `min_ncc` | float | 0.25 | 接受匹配的最小 NCC。 |
| `sgm_p1` / `sgm_p2` | float | 0.06 / 0.35 | SGM 平滑惩罚。 |
| `max_views` / `min_views` | int | 10 / 2 | 每格参与的最大/最小视图数。 |
| `top_k_views` | int | 4 | 取最好的 K 个视图平均代价。 |
| `dense_tile` | int | 384 | 物方分块边长，便于并行与续跑。 |
| `workers_dense` | int\|null | CPU-1 | 密集匹配进程数。 |

#### S4_dsm — DSM

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `spike_median_cells` | int | 5 | 粗差中值滤波窗口。 |
| `spike_tolerance_m` | float | 2.5 | 相对中值超过此高差视为尖刺剔除。 |
| `min_confidence` | float | 0.02 | 低于此置信度的格网置无效。 |
| `max_fill_gap_m` | float | 40.0 | 空洞填充最大跨度（米）。 |
| `dsm_nodata` | float | -3.4028235e+38 | GeoTIFF nodata，对齐商业成品。 |
| `write_dtm` | bool | false | 是否额外输出 DTM（形态学 opening）。 |
| `dtm_opening_m` | float | 12.0 | DTM opening 尺度（米）。 |

#### S5_ortho — 正射与融合

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `bands` | string[] | 全部 8 组 | 参与正射的波段。**仅 RGB 快速预览**时设为 `["Color"]`，大幅缩短 S5。 |
| `ortho_over_dsm` | float | 0.5 | 正射 GSD = DSM GSD × 该系数（商业 1:2）。 |
| `max_tilt_deg_ortho` | float | 60.0 | 正射选片时最大倾角。 |
| `occlusion_tolerance_m` | float | 0.6 | Z-buffer 遮挡容差（米）。 |
| `color_correction` | enum | `off_for_ms` | RGB 可开增益均衡；多光谱强制禁用保辐射。 |
| `blend_levels` | int | 5 | 多频段融合层数（「中/高」档映射）。 |
| `seamline_enabled` | bool | true | 是否计算拼接线。 |
| `seamline_export_geojson` | bool | true | 导出拼接线矢量便于核查。 |
| `workers_ortho` | int\|null | 同 workers | 分波段/分块正射并行度。 |

#### S6_report — 报告

| key | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `write_pdf_report` | bool | true | 生成 `质量报告.pdf`。 |
| `write_json_report` | bool | true | 生成机器可读质量 JSON。 |
| `report_include_figures` | bool | true | PDF 是否含轨迹/误差等附图（不含全部中间巨图）。 |

### 5.3 CLI 映射（兼容）

现有 CLI 标志继续有效，并与 snapshot 键对齐：

| CLI | snapshot key |
| --- | --- |
| `--input` | `input_dir` |
| `--out` | `output_dir` |
| `--max-frames` | `max_frames` |
| `--max-index` | `max_index` |
| `--dsm-gsd` | `dsm_gsd` |
| `--workers` | `workers_dense` / 回退通用 workers |
| `--cache-dir` | `cache_dir` |
| `--bands` | `bands`（逗号分隔；仅 RGB：`Color`） |
| `--reuse-dsm` | `reuse_dsm` |
| `--run-mode` | `run_mode` |
| `--start-stage` | `start_stage` |
| `--stop-after-stage` | `stop_after_stage` |

可选增强（不破坏旧用法）：`--job-id`、`--params-json`、`--profile-id`（需 API 可达时拉模板）。

---

## 6. 任务状态机与进度

### 6.1 状态

`draft` → `queued` → `running` ⇄ `paused` → `running` → `succeeded`  
旁路：`running` → `awaiting_continue` →（继续）→ `queued`/`running` → …  
分支：`cancelled`、`failed`。

- **`awaiting_continue`**：按 `run_mode` 在阶段成功边界停下，等人确认；不自动进下一阶段。  
- **`succeeded`**：到达最终目标（`full` 跑完 S6，或用户明确结束）才标记成功。若在 `awaiting_continue` 用户选择「结束任务」，记为 `succeeded`（部分完成）并在记录中标注 `completed_stage`。

不变量：任意时刻最多一个 `running`（DB 约束或 advisory lock + worker 单实例约定）。

### 6.2 暂停 / 续跑 / 取消 / 阶段继续

- **pause**：backend → `mosaic.control`；worker 设标志；在阶段边界写检查点后置 `paused`。  
- **resume（暂停恢复）**：从暂停点继续当前计划。  
- **continue（阶段继续）**：仅 `awaiting_continue` → 投递下一阶段（或到新的 `stop_after_stage`）。  
- **cancel**：控制消息 + 终止子进程组；置 `cancelled`。

### 6.3 进度载荷

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "stage_id": "S3_dense",
  "stage_progress": 42.5,
  "global_percent": 55.0,
  "message": "密集匹配 tile 12/40",
  "eta_seconds": 3600,
  "ts": "2026-09-18T12:00:00+08:00"
}
```

ETA：同 profile、相近 `n_shots` 的历史阶段耗时滑动估计；不足时 `eta_seconds` 为 null。

前端：WebSocket 推送；断线用 `GET /api/jobs/{id}` 补偿。

---

## 7. RabbitMQ 契约

| 实体 | 名称 | 方向 |
| --- | --- | --- |
| 队列 | `mosaic.jobs` | backend → worker |
| 队列 | `mosaic.control` | backend → worker |
| 队列 | `mosaic.progress` | worker → backend |
| 队列 | `mosaic.events` | worker → backend |

- 消息：JSON + `schema_version` + `job_id`。
- 消费：手动 ack；失败进入 DLQ（`mosaic.jobs.dlq`）并标 `failed`。
- 幂等：worker 对同一 `job_id` 若已 `succeeded` 则忽略；`running` 重复投递需 fencing token（`run_attempt` 整型递增）。

---

## 8. 数据模型（PostgreSQL 要点）

- `users` / `roles` / `user_roles`
- `param_definitions` / `param_profiles` / `param_profile_values`
- `job_runs`：状态、时间线、`params_snapshot`、路径字段（input/output/cache/log/process/products/report）、`error_summary`、`n_shots`、阶段耗时 JSONB
- `job_progress_latest`：最新进度（可冗余在 `job_runs`）
- `audit_logs`：谁在何时做了何写操作
- `system_settings`：`data_roots` 白名单数组等

路径安全：创建任务时 resolve 路径，必须位于 `data_roots` 之下；`output` 不得落在 `input` 之内（与现 `pipeline` 断言一致）。

Backend 挂载 `DATA_ROOT`：用于存在性校验、日志尾部读取、PDF 下载；**不**在容器内执行 `run_mosaic`。

---

## 9. API 与前端信息架构

### 9.1 API（节选）

- `POST /api/auth/login` → JWT  
- CRUD `/api/param-definitions`（admin）、`/api/profiles`  
- `POST /api/jobs`（executor）：profile + 路径覆盖 + `run_mode`/`start_stage`/`stop_after_stage`/`bands` → 入队  
- `POST /api/jobs/{id}/pause|resume|cancel|continue`  
  - `continue` body 可选：`stop_after_stage`（本次继续跑到哪）  
- `GET /api/jobs`、`GET /api/jobs/{id}`  
- `GET /api/jobs/{id}/logs?tail=`  
- `GET /api/jobs/{id}/report.pdf`  
- `GET /api/docs/cli-guide`：返回命令行教学 Markdown（与前端教学页同源）  
- `WS /ws/jobs/{id}`  

### 9.2 前端页面

1. 登录  
2. 参数配置（阶段折叠 + 说明 + 保存模板；**仅 RGB 预设**；`run_mode` / 起止阶段）  
3. 执行台（选模板、路径、启停控、**继续下一阶段**、步骤条、总进度、ETA；等待确认时高亮当前已完成阶段）  
4. 执行记录（列表 + 详情：快照、目录、日志、报告下载、`completed_stage`）  
5. **命令行使用教学**（独立页，只读开放给所有登录角色；可导出/复制命令）  
6. 用户与审计（admin）  

### 9.3 命令行教学页内容（必须覆盖）

- 不部署 `visual_server` 时如何仅用 `source` 运行。  
- 环境依赖、`run.sh` / `python -m ms_mosaic` 完整示例。  
- **仅 RGB**：`--bands Color`。  
- **跑到某阶段**：`--run-mode until_stage --stop-after-stage S4_dsm`。  
- **逐步确认**：`--run-mode step`（每阶段结束退出码区分「阶段完成待继续」与「全流程成功」，文档写明）。  
- 从检查点续跑：`--start-stage S5_ortho --reuse-dsm …`。  
- 输入只读、输出不得落在输入内等安全约定。  
- 常见问题：无 POS、白板过滤、路径、QGIS 打开成果。  

语言：简体中文 UI。

---

## 10. source 改动边界

1. `ProgressReporter` 协议：阶段开始/进度/结束；默认打印或静默，不影响 CLI。  
2. 阶段边界调用 `checkpoint()`；轮询 `ControlState`；支持 `stop_after_stage` / `run_mode=step` 在边界退出为「待继续」。  
3. `run_mosaic`（或编排层）按 `start_stage`/`stop_after_stage`/`bands` 裁剪执行；缺检查点时失败信息明确。  
4. `python -m ms_mosaic.worker_main`：连接 MQ，组装 kwargs，调用编排入口。  
5. **禁止**把 visual_server 依赖写进热路径（无强制 import pika）；worker 入口单独依赖。  
6. CLI 教学文案以 `source/README` 或 `docs/cli-usage.md` 为源，前端教学页引用同一份，避免两套说明漂移。  

---

## 11. 安全与生产运维

- JWT + 角色鉴权；密码哈希（bcrypt/argon2）。  
- CORS 仅配置的前端源。  
- RabbitMQ / PG 强密码，仅 compose 网络暴露；生产映射端口按需。  
- 默认管理员首次由 env `BOOTSTRAP_ADMIN_*` 创建。  
- 备份：PG volume 定期备份；`params_snapshot` 保证历史可重放理解。  
- 健康检查：`/api/health`（PG + RabbitMQ 连通性）。  

---

## 12. 测试策略

- backend：参数校验、状态机转换、单 running 约束、路径白名单单测。  
- worker：mock MQ 的进度上报与 cancel 在阶段边界生效。  
- e2e（可选）：compose up + 最小 fixture 目录跑通 S0–S1 冒烟（全量空三不进 CI）。  
- 前端：关键表单与进度组件单测。  

---

## 13. 实施分期（仍属同一交付，便于排期）

| 期 | 内容 |
| --- | --- |
| P0 | compose + PG 迁移 + 参数字典种子 + 用户角色 + profiles CRUD UI + **仅 RGB 预设** |
| P1 | jobs 入队 + worker + 进度 WS + 执行台进度条 + **run_mode/起止阶段** |
| P2 | pause/resume/cancel/**continue** + 检查点 + 执行记录溯源 + 报告/日志下载 + **命令行教学页** |
| P3 | ETA 历史估计、审计完善、运维脚本与 README 硬化 |

P0–P2 为生产可用最小集；P3 为增强。

---

## 14. 成功标准

1. 配置员可按阶段理解并保存模板；每参数有中文详细说明；可一键「仅 RGB」。  
2. 执行员可 `until_stage` / `step` 阶段性运行；阶段成功后需点「继续」才进入后续；失败不自动往下跑。  
3. 执行员页面可全流程跑通；同时仅一任务 `running`；可取消/暂停。  
4. 任意历史任务可查看参数快照与目录、下载质量报告、查看日志。  
5. 无 visual_server 时 CLI 可用；前端「命令行教学」页与 CLI 文档一致。  
6. `docker compose up` 拉起 PG/RabbitMQ/前后端；worker 用宿主机命令启动。  

---

## 15. 开放实现细节（不阻塞规格）

- 检查点文件精确命名在实现计划中与现有 `cache/` 布局对齐。  
- 前端组件库选用 Ant Design 或 MUI 之一（实现计划锁定）。  
- worker 与 Docker RabbitMQ 的主机名：开发用 `host.docker.internal` / `localhost` 映射，写入 `.env.example`。  
