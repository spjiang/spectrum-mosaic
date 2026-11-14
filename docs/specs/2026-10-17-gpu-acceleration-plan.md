# 拼图 GPU 加速实施计划（第一期：基础设施 + 正射）

> 设计：`2026-10-17-gpu-acceleration-design.md`
> 本期范围：GPU 识别与选卡、CPU/GPU 切换层、参数全链路、GPU 部署文件、GPU 正射。
> 密集匹配、特征匹配、平差三块各自单独出计划，等本期在 T4 上验收后再开。

**目标：** 新建处理能配置 GPU；worker 能识别显卡并自动退回 CPU；正射重采样有经过对拍验收的 GPU 实现。

**结构：** 新增 `gpu.py`（探测、选卡、显存预算，纯函数可在无卡机器测试）和 `xp.py`（numpy/cupy 切换）。GPU 参数沿用 `cpus` 的全链路做法。GPU 正射单独成 `ortho_gpu.py`，签名与 CPU 版一致。

**技术栈：** Python 3.11、CuPy（`cupy-cuda12x`）、FastAPI、PostgreSQL、React + antd、pytest。

## 全局约束

- 不改 `parallel.py`（另一会话负责）；`pipeline.py` 只做最小接线。
- 没有 CuPy 或没有卡时，所有现有测试照常通过；GPU 测试用 `@pytest.mark.gpu` 标记，自动跳过。
- 参数 key：`gpu`（string，默认 `auto`，取值 `auto` / `off` / 卡号），`gpu_memory_gb`（float，默认 0 = 自动，取空闲显存的 85%）。
- 计算能力低于 7.0 的卡不用。
- 一个阶段的 GPU 实现，没进验收白名单之前，`gpu=auto` 也走 CPU。
- 198 上只用 `/home/spectrum-mosaic/`；不建 Docker 镜像、不动根分区和 Docker 配置；限 32 核、64 GB。
- 代码注释、日志、界面文字用中文；按项目规则不主动提交 git。

---

### 任务 1：198 环境验证（需要 VPN）

- 在 `/home/spectrum-mosaic/` 装 micromamba，建 Python 3.11 环境，装 `requirements-worker.txt` 与 `cupy-cuda12x`。
- 验证：`python -c "import cupy; print(cupy.cuda.runtime.getDeviceCount())"` 输出 2；worker 现有测试全部通过。
- 失败（如 glibc 2.17 装不上）立即回报，不做任何系统级改动。

### 任务 2：`gpu.py` 探测与选卡

**文件：** 新建 `worker/ms_mosaic/gpu.py`，测试 `worker/tests/test_gpu.py`

**接口：**
- `GpuInfo(index, name, total_mb, free_mb, capability)`
- `GpuChoice(device: int | None, budget_bytes: int, name: str, reason: str)`
- `parse_gpu_setting(raw) -> str`：返回 `auto` / `off` / 卡号字符串，非法值按 `auto`
- `probe() -> tuple[list[GpuInfo], str | None]`：没有可用卡时第二项是原因
- `choose(setting, memory_gb, gpus, missing_reason, *, stage, validated) -> GpuChoice`：纯函数
- `set_job_gpu(setting, memory_gb)`、`select(stage) -> GpuChoice`：任务级入口
- `describe(stage_label, choice) -> str`：日志行
- `VALIDATED_STAGES: frozenset[str]`：通过验收的阶段白名单，初始为空

测试覆盖：`off`、无卡、计算能力不足、`auto` 选空闲最多、指定卡不存在、显存上限取小值、未验收阶段走 CPU、`probe` 在没有 CuPy 时给出原因。

### 任务 3：`xp.py` 切换层

**文件：** 新建 `worker/ms_mosaic/xp.py`，测试 `worker/tests/test_xp.py`

**接口：** `array_module(device) -> module`、`asnumpy(a) -> np.ndarray`、`on_device(device)` 上下文。`device=None` 时返回 numpy，上下文不做任何事。

### 任务 4：参数全链路

- `db/migrations/017_gpu.sql`：登记 `gpu`、`gpu_memory_gb`（`S0_io`，排序 27、28），`job_runs` 加 `gpu TEXT`、`gpu_memory_gb DOUBLE PRECISION`，并从快照回填。
- `backend/app/bootstrap.py`：`_ensure_gpu_params`、`_ensure_job_gpu`，照搬 `cpus` 写法。
- `backend/app/models.py`、`schemas.py`、`routers/jobs.py`：两列进模型、输出和列表查询。
- `backend/app/services/jobs.py`：`parse_gpu`、`parse_gpu_memory_gb`，在所有写 `cpus` 的地方同步写入；快照日志加 `GPU=`。
- `backend/app/param_catalog.py`：中文标签。
- `worker/ms_mosaic/__main__.py`：`--gpu`、`--gpu-memory-gb`。
- `worker/ms_mosaic/stage_runner.py`：`set_job_gpu(...)`，`finally` 里清掉。
- 测试：后端解析函数、CLI 参数映射、`stage_runner` 设置与清理。

### 任务 5：前端与计算节点页

- `worker_status.py`：心跳里加 `gpus` 字段（`gpu.summary()`：读 `nvidia-smi`，结果缓存 60 秒）。不用 `gpu.probe()`，因为 CuPy 探测会在心跳进程里建 CUDA 上下文，每张卡常驻占几百 MB 显存。
- `frontend/src/pages/Execute.tsx`：CPU、内存旁加「GPU」下拉（自动 / 不用 GPU / 卡列表）和「显存上限」滑块。
- `Worker.tsx`：显卡列表；`JobDetailModal.tsx`、`JobSnapshot.tsx`、`Jobs.tsx`、`paramHelp.ts`：展示与说明。
- 验证：`npm run build` 通过；页面手工检查。

### 任务 6：GPU 部署文件与文档

- `worker/Dockerfile.gpu`、`docker-compose.gpu.yml`、`worker/requirements-gpu.txt`。
- `参数说明.md`、`cli-usage.md`、`server/README.md`、`算法出处.md` 同步。

### 任务 7：GPU 正射（需要 198）

- `worker/ms_mosaic/ortho_gpu.py`：与 `ortho.orthorectify_tile` 同签名；坐标先减瓦片原点再转 float32，投影中间量 float64。
- 阶段入口按 `gpu.select("S5_ortho")` 选择实现；GPU 进程每卡 1 个，瓦片成批送入，显存不足批量减半，减到 1 仍不足则本阶段退回 CPU。
- 测试：`@pytest.mark.gpu` 合成场景对拍（覆盖掩膜一致；≥99.9% 像元差 ≤1 DN，最大 ≤2 DN）；小窗口验证台；S810 子集全流程读 `比对报告.txt`。
- 三层全过后，把 `S5_ortho` 加进 `VALIDATED_STAGES`。
