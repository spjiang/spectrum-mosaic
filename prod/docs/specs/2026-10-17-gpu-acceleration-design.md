# 拼图 GPU 加速设计规格

> 状态：待评审
> 日期：2026-10-17
> 范围：`prod/server`（worker / backend / frontend / db / compose）
> 性质：生产交付。客户现场为 RTX 4090，目标 S810 全规模（约 2000 张、8 波段、全分辨率）25 分钟。

---

## 1. 目标与非目标

### 1.1 本期目标

1. 处理方案和新建处理里增加 GPU 配置，与 CPU、内存并列：选卡（自动 / 不用 / 指定卡）和显存上限。
2. Worker 能识别显卡；没有卡、驱动不对或显存不够时，自动退回 CPU，任务不因 GPU 失败。
3. 按顺序把四个热点改成 GPU 路径：正射重采样 → 密集匹配 → 特征提取与匹配 → 平差。每一块都要过质量闸门（第 6 节）才能进主路径。
4. 每次实际使用的设备、显存上限、退回原因，写进日志和参数快照，处理记录可复现。

### 1.2 非目标（本期不做）

- 本期不考核速度。验证机是 2×Tesla T4，算力约为 4090 的十分之一，T4 上的耗时不能换算成 4090。25 分钟目标要到 4090 上实测，另行验收。
- 单个任务同时用多张卡。本期一个任务只用一张卡，多卡另立项。
- 开发需求清单里的另外 6 项（匀色/裁边开关、DSM 按分辨率分算法、人工编辑拼接线、分辨率记录与下拉、评估、节点查缺补漏），各自单独出设计。
- `parallel.py` 里正在收尾的内存账本和回退逻辑，由另一个会话负责。本设计只通过 `map_tiles` 的现有接口接入，不改它的内部实现。

---

## 2. 已确认决策

| 项 | 选择 |
| --- | --- |
| 实现方案 | 方案 A：在现有模块里加 GPU 路径，算法和方程不变，CPU 代码保留作对照和兜底 |
| GPU 计算库 | CuPy（MIT 许可证）+ 必要的自写 CUDA 内核 |
| 配置方式 | 默认自动，可在新建处理时手动选卡、设显存上限 |
| 验证机 | 10.17.19.198：2×Tesla T4 16 GB，驱动 535 / CUDA 12.2，64 核，125 GB 内存 |
| 验证机约束 | 只用 `/home` 剩余空间（约 37 GB），根分区不新增占用；拼图限 32 核、64 GB，不能影响机器上的 Dify 服务 |
| 速度目标 | 本期以 T4 上的功能和正确性为准，速度另验收 |
| 对外许可证 | 不引入 AGPL/GPL 组件；第三方 GPU 实现先查许可证，能商用才用 |

选方案 A 的原因：

- **许可证：** OpenMVS 是 AGPL-3.0，不能进闭源商业交付。CuPy 是 MIT，核心代码自有。
- **业界做法：** Metashape、Pix4D 都是自研 GPU 加速，能选显卡，也能只用 CPU。
- **质量可控：** 每个环节有出处，可以和 CPU 对拍、和商业成果比对，出问题能定位到模块。

---

## 3. 总体结构

```text
新建处理（前端）  gpu / gpu_memory_gb
      │
backend：param_definitions + job_runs 列 + params_snapshot
      │  RabbitMQ
worker：stage_runner → pipeline
      │
      ├─ gpu.py      探测显卡、选卡、显存预算、退回原因
      ├─ xp.py       数组运算切换层（numpy / cupy）
      └─ 各模块的 GPU 实现，单独成文件：
           ortho_gpu.py    正射反投影与取样
           dense_gpu.py    平面扫描代价与 SGM 聚合
           features_gpu.py SIFT 检测与描述
           matching_gpu.py 精确 kNN + 比值检验
           at_gpu.py       Schur 补法方程求解（可选）
```

原有的 `ortho.py`、`dense.py`、`features.py`、`matching.py`、`at.py` 保持 CPU 实现不变。阶段入口按 `gpu.py` 的决定调用 GPU 版或 CPU 版，两者输入输出签名相同。

---

## 4. GPU 资源配置与识别

### 4.1 参数

| key | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| `gpu` | string | `auto` | `auto`：选空闲显存最多的一张卡；`off`：只用 CPU；`0`、`1`…：指定卡号 |
| `gpu_memory_gb` | float | `0` | 本任务显存上限。0 表示自动，取所选卡启动时空闲显存的 85% |

- 新增迁移 `017_gpu.sql`：在 `param_definitions` 里登记这两个参数（阶段 `S0_io`，排序紧跟 `cpus`），`job_runs` 增加 `gpu TEXT`、`gpu_memory_gb DOUBLE PRECISION` 两列，并从已有 `params_snapshot` 回填。写法照搬 `009_cpus.sql`、`010_job_cpus.sql`。
- CLI 增加 `--gpu`、`--gpu-memory-gb`，名字与 Web 参数 key 一致。

### 4.2 探测（`gpu.py`）

- 先 `import cupy`，失败就记退回原因「未安装 GPU 运行库」。
- 用 `cupy.cuda.runtime` 枚举卡号、型号、总显存、当前空闲显存、计算能力。
- 计算能力低于 7.0 的卡不用（T4 是 7.5，4090 是 8.9）。
- 遵守 `CUDA_VISIBLE_DEVICES`。部署时由它限定 worker 能看到哪些卡。
- 探测结果写进 worker 的 `status.json`，「计算节点」页按现有 CPU、内存的样式展示卡列表。

### 4.3 任务内的决定

阶段开始时调用 `gpu.select(stage)`，返回 `设备号 | None` 和一条原因。规则：

1. `gpu=off`，或者没有可用的卡 → CPU。
2. `gpu=auto` → 空闲显存最多的卡；`gpu=N` → 第 N 张，不存在则退回 CPU 并记原因。
3. 显存预算 = `gpu_memory_gb`（或自动值），且不超过该卡此刻的空闲显存。
4. 日志各写一行，例如：
   - `密集匹配：GPU 0 Tesla T4，显存上限 13.1 GB`
   - `正射：未检测到可用 GPU（未安装 GPU 运行库），使用 CPU`

### 4.4 显存与进程

- 每个用 CUDA 的进程会单独占一份上下文（约 300–500 MB），所以 GPU 计算不能放进 CPU 那种多进程瓦片池。
- 每张选中的卡只开 1 个 GPU 工作进程，瓦片按批送进去。读写影像、编码 GeoTIFF 仍由 CPU 进程池负责。
- 批大小按显存预算换算。遇到显存不足（`cupy.cuda.memory.OutOfMemoryError`）时，批大小减半重试；减到 1 块仍不够，这一阶段退回 CPU，并在日志里写明。
- 阶段结束时释放 CuPy 内存池，不带进下一阶段。

---

## 5. 四个阶段的 GPU 化

### 5.1 正射重采样（`ortho_gpu.py`），第一个做

- **内容：** 输出格网每个像元，按 DSM 高程和相机模型（Brown–Conrady 畸变）反投影到像方，双线性取样；Z-buffer 判遮挡；保留现有的像幅外射线剔除规则（归一化半径超过像幅四角 1.15 倍判无效）和有效权重归一。
- **出处：** Kraus《Photogrammetry》；Amhar et al. 1998（真正射 Z-buffer）。GPU 只是逐像元并行，方程不变。
- **精度要点：** UTM 坐标有 6–7 位整数，float32 只有约 0.06 m 分辨率，相当于正射 GSD（0.054 m）一个像元。所以坐标先减去瓦片原点，再转 float32 计算；相机投影的中间量用 float64。
- **验收：**
  - 有效覆盖掩膜与 CPU 版完全一致；
  - 量化成 DN 后，≥99.9% 的有效像元差值 ≤1 DN，最大差值 ≤2 DN。

### 5.2 密集匹配（`dense_gpu.py`）

- **内容：** 物方平面扫描的匹配代价，加 SGM 两级惩罚的多方向聚合；分层（粗层定位、细层精化）的逻辑不变。
- **出处：** Gallup et al., CVPR 2007（原文即 GPU 实时平面扫描）；Hirschmüller, PAMI 2008（SGM）；Ernst & Hirschmüller, ISVC 2008（GPU 版 SGM）。
- **验收：**
  - 与 CPU 版相比，高程差的中位数 ≤0.01 m，≥99% 的格网差值不超过一个高程步长；
  - 小窗口验证台上，与商业 DSM 比较的解出率和中误差不低于 CPU 版。

### 5.3 特征提取与匹配（`features_gpu.py`、`matching_gpu.py`）

- **匹配：** GPU 精确 kNN（k=2）加 Lowe 比值检验，替代现在的 FLANN kd-tree 近似搜索。精确搜索结果不比近似搜索差，但不会和 CPU 逐位相同。出处：Lowe, IJCV 2004；Johnson et al., *Billion-scale similarity search with GPUs*, 2017。
- **特征：** GPU SIFT 检测，描述子照常做 RootSIFT。出处：Lowe 2004；Wu, SiftGPU 2007。GPU 检测出的点和 OpenCV 不完全一样，这是本期最大的质量风险。
- **许可证：** 先查 SiftGPU、CudaSift 等现成实现能否商用。不能就按 Lowe 2004 自己写 CUDA 版。查证结果写进 `算法出处.md`。
- **验收（不比逐点一致，比空三结果）：**
  - 连接点数 ≥ CPU 版的 95%；
  - 平差重投影 RMS ≤ CPU 版 +0.05 px，且绝对值 ≤0.5 px（LiMapper 报告为 0.41 px）；
  - 相机内参与 CPU 版在第 6.2 节的容差内。

### 5.4 平差（`at_gpu.py`），最后做、可选

- **内容：** Schur 补后的相机块法方程，在 GPU 上组装和 Cholesky 分解；Huber 核、分阶段释放畸变、GNSS 约束都不变。
- **出处：** Triggs et al. 1999；Wu et al., *Multicore Bundle Adjustment*, CVPR 2011。
- **前提：** 先在 198 上测出 S810 空三的耗时构成。平差占比不高就不做。
- **验收：** 收敛后 RMS 与 CPU 版相差 ≤1e-3 px，外方位元素相对差 ≤1e-6。

---

## 6. 验证

### 6.1 三层

1. **单元对拍：** 合成场景（`tests/synthetic.py`）上 GPU 与 CPU 逐项比较，用 `@pytest.mark.gpu` 标记。没有 CuPy 或没有卡时自动跳过；在 198 上必须全部通过。
2. **小窗口验证台：** 用已有验证台，在 MAX_20251017 的一块小区域上直接和商业 DSM、商业正射比较。
3. **全量交付：** 在 198 上，用 `/home` 的空间跑一个能放得下的 S810 子集（按 37 GB 剩余空间确定影像数）。跑完读 `比对报告.txt` 的「未达标」行，GPU 版的规格项必须与 CPU 版一样全部通过。

### 6.2 容差汇总

| 阶段 | 指标 | 门槛 |
| --- | --- | --- |
| 正射 | 覆盖掩膜 | 完全一致 |
| 正射 | 有效像元 DN 差 | ≥99.9% 的像元 ≤1，最大 ≤2 |
| 密集 | 高程差 | 中位数 ≤0.01 m；≥99% 不超过一个高程步长 |
| 特征匹配 | 连接点数 | ≥ CPU 版的 95% |
| 特征匹配 | 重投影 RMS | ≤ CPU 版 +0.05 px，且 ≤0.5 px |
| 平差 | RMS | 与 CPU 版相差 ≤1e-3 px |

每块 GPU 实现，三层验证全部通过后，才在 `gpu=auto` 时默认启用；未通过的阶段即使有卡也走 CPU。

---

## 7. 部署

### 7.1 镜像与 compose

- 新增 `worker/Dockerfile.gpu`：基于 `python:3.11-slim`，额外安装 `cupy-cuda12x` 及其所需的 CUDA 运行库 wheel，不使用体积很大的 CUDA 官方基础镜像。
- 新增 `docker-compose.gpu.yml` 覆盖文件：worker 使用 `Dockerfile.gpu`，并通过 `deploy.resources.reservations.devices` 申请 NVIDIA 设备。
- 默认的 `docker-compose.yml` 不变，Mac 开发机和没有显卡的机器照常运行。
- 客户 4090 现场：`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`。

### 7.2 验证机 198

- 根分区只剩 9.2 GB，上面还跑着 Dify 的数据库，**本期不在 198 上构建或拉取 Docker 镜像**。
- 在 `/home/spectrum-mosaic/` 下用 micromamba 建独立 Python 环境，安装 worker 依赖和 CuPy；代码、数据、缓存、输出全部放在这个目录下。
- 跑 CLI（`python -m ms_mosaic`），用 `taskset` 绑 32 核，再用 `systemd-run --scope -p MemoryMax=64G` 限制内存，用 `CUDA_VISIBLE_DEVICES` 指定显卡。
- CentOS 7 的 glibc 是 2.17，部分新版 wheel 可能装不上。实施计划的第一步就是在 198 上验证环境能否装起来；装不起来要先回报，不得改动根分区或 Docker 配置。

---

## 8. 风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| T4 与 4090 算力差约 10 倍 | 速度目标本期验证不了 | 本期只验正确性；4090 上另行验收 |
| GPU SIFT 与 OpenCV 结果不同 | 空三质量可能变化 | 按空三结果验收（5.3），不达标就保留 CPU 特征提取，只上 GPU 匹配 |
| 第三方 GPU SIFT 许可证不允许商用 | 不能直接用 | 自写 CUDA 实现，工期增加 |
| 198 磁盘只有 37 GB 可用 | 跑不了 S810 全量 | 用子集做全量流程验证；全规模放到 4090 环境 |
| 198 是共用生产机 | 可能拖慢 Dify | 限 32 核、64 GB；不动根分区和 Docker |
| CentOS 7 glibc 2.17 | 依赖可能装不上 | 实施第一步先验证，失败即回报 |
| `parallel.py` 正由另一会话修改 | 接入点可能变化 | GPU 正射等那边收尾后再接入，只用 `map_tiles` 的公开接口 |

---

## 9. 文档同步

- `算法出处.md`：增加「GPU 加速」相关行，写明每个 GPU 实现的出处和许可证查证结论。
- `worker/docs/参数说明.md`、`cli-usage.md`：补充 `gpu`、`gpu_memory_gb` 两个参数。
- `prod/server/README.md`：补充 GPU 部署方式（第 7.1 节）。
