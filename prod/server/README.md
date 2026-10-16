# 多光谱拼图管控台（server）

本目录即整套服务：`frontend`、`backend`、`worker`，外加 PostgreSQL / RabbitMQ。  
Worker 映射主程序源码（容器 `/app`）。数据目录与 worker 同级：`server/data` → 容器 `/data`。

## 数据目录

```text
server/
  worker/                 主程序源码 → 容器 /app
  data/
    input/<测区名>/<架次>/  →  /data/input/<测区名>/<架次>
                            例：MAX_20251017/MAX_20251017_001
    output/runs/<任务>/     →  /data/output/runs/<任务>
```

可视化里输入填 `/data/input/MAX_20251017/MAX_20251017_001`，输出填 `/data/output/runs/任务名`。后端和 Worker 用同一套路径。新测区拷进 `server/data/input/<测区名>/<架次>/`。本机已有架次可通过 `.env` 的 `DATASET_MAX_20251017` 挂到该路径。
## 快速启动

```bash
cd algorithm/projects/数据融合/prod/server
./scripts/dev_up.sh
```

或：

```bash
cd algorithm/projects/数据融合/prod/server
cp .env.example .env
# 按需改 DATASET_MAX_20251017 / JWT_SECRET / 密码
docker compose up -d --build
```

- 前端：http://localhost:8080  
- API：http://localhost:8000/api/health  
- RabbitMQ 管理：http://localhost:15672 （mosaic / mosaic_secret）  
- 默认管理员：`admin` / `admin123`（请立刻修改）

不要同时再开本机 `run_worker.sh`，否则两个 Worker 会抢同一条队列。

## 本机 Worker（仅调试主程序、不走容器时）

```bash
cd /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker
export RABBITMQ_URL=amqp://mosaic:mosaic_secret@127.0.0.1:5672/
./scripts/run_worker.sh
```

Worker 消费 `mosaic.jobs`，调用 `run_stages`，回传进度到 `mosaic.progress` / `mosaic.events`。

## 功能要点

| 能力 | 说明 |
| --- | --- |
| 按阶段参数 | 参数配置页分组 + 中文说明 |
| 仅 RGB | 预设 `rgb_preview` / `bands=["Color"]` |
| 调试对照 | `benchmark_dir` 只写比对报告，不改出图；`match_reference_color` 默认关 |
| 阶段控制 | `run_mode`: full / until_stage / step；执行台「继续下一阶段」 |
| 溯源 | `job_runs.params_snapshot` + 目录字段 + 日志/报告下载 |
| CLI 教学 | 前端「命令行教学」页 = `worker/docs/cli-usage.md` |

## 不部署可视化时

见 `worker/docs/cli-usage.md`，例如：

```bash
PY="/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/source/.venv/bin/python"
cd /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker
"$PY" -m ms_mosaic --output-dir /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb --bands Color
"$PY" -m ms_mosaic --output-dir /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/dbg_until_dsm --run-mode until_stage --stop-after-stage S4_dsm
```

## 开发后端（无 compose）

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://mosaic:mosaic_secret@127.0.0.1:5432/mosaic_visual
export RABBITMQ_URL=amqp://mosaic:mosaic_secret@127.0.0.1:5672/
uvicorn app.main:app --reload --port 8000
```

## 测试

```bash
# 主程序
cd worker && PYTHONPATH=. pytest tests/test_progress_control.py -q

# backend（需在 backend 目录，PYTHONPATH=.）
cd backend && PYTHONPATH=. pytest tests/test_job_paths.py -q
```
