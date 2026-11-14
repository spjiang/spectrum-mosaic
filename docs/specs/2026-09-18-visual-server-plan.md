# visual_server 多光谱拼图管控台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付生产可用的 `prod/visual_server`（React + FastAPI + PG + RabbitMQ）与宿主机 `ms_mosaic` worker 钩子，支持阶段参数配置、仅 RGB、阶段性运行控制、执行溯源与 CLI 教学页；原 CLI 保持可用。

**Architecture:** Compose 只跑 postgres/rabbitmq/backend/frontend；宿主机 worker 消费 `mosaic.jobs` 调用编排层；进度经 MQ → backend → WebSocket；参数与 `job_runs.params_snapshot` 落 PG。

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic + Pika；React 18 + Vite + TypeScript + Ant Design；PostgreSQL 16；RabbitMQ 3；Docker Compose。

## Global Constraints

- 路径：`algorithm/projects/数据融合/prod/visual_server/` 与 `source/` 同级；计算不进 Docker。
- 同时最多一个 `running`；支持 `awaiting_continue` / 仅 RGB / `run_mode`.
- CLI `python -m ms_mosaic` 无 visual_server 仍可跑。
- UI 与文档简体中文；规格见 `prod/docs/specs/2026-09-18-visual-server-design.md`。

---

## File Map

```text
prod/visual_server/
  docker-compose.yml
  .env.example
  README.md
  db/migrations/001_init.sql
  backend/
    Dockerfile
    requirements.txt
    app/main.py
    app/config.py
    app/db.py
    app/models.py
    app/auth.py
    app/schemas.py
    app/seed_params.py
    app/routers/{auth,profiles,jobs,system,docs}.py
    app/services/{jobs,mq,progress_ws}.py
    tests/
  frontend/
    Dockerfile
    package.json
    src/main.tsx
    src/App.tsx
    src/api.ts
    src/auth.tsx
    src/pages/{Login,Profiles,Execute,Jobs,CliGuide,Admin}.tsx
    src/components/{StageForm,JobProgress}.tsx
prod/source/
  docs/cli-usage.md
  ms_mosaic/progress.py
  ms_mosaic/control.py
  ms_mosaic/stage_runner.py
  ms_mosaic/worker_main.py
  ms_mosaic/__main__.py          # 增加 stage/bands/run-mode 参数
  tests/test_stage_runner.py
  tests/test_progress_control.py
```

---

### Task 1: Compose + Backend 骨架 + 健康检查

**Files:**
- Create: `prod/visual_server/docker-compose.yml`
- Create: `prod/visual_server/.env.example`
- Create: `prod/visual_server/backend/{Dockerfile,requirements.txt,app/main.py,app/config.py}`
- Create: `prod/visual_server/backend/tests/test_health.py`

**Interfaces:**
- Produces: `GET /api/health` → `{status, postgres, rabbitmq}`；compose 服务名 `postgres`/`rabbitmq`/`backend`/`frontend`

- [ ] **Step 1: 写 failing 健康检查测试（可在无 docker 时用 TestClient mock）**

```python
from fastapi.testclient import TestClient
from app.main import app
def test_health_shape():
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
```

- [ ] **Step 2: 实现 FastAPI app + config（DATABASE_URL, RABBITMQ_URL, JWT_SECRET, DATA_ROOTS, BOOTSTRAP_ADMIN）**
- [ ] **Step 3: docker-compose 四服务 + backend Dockerfile**
- [ ] **Step 4: 跑通 `pytest backend/tests/test_health.py`；Commit**

---

### Task 2: PG Schema + 参数种子 + Auth

**Files:**
- Create: `prod/visual_server/db/migrations/001_init.sql`
- Create: `prod/visual_server/backend/app/{models.py,db.py,auth.py,seed_params.py,routers/auth.py,routers/profiles.py}`
- Test: `backend/tests/test_auth_profiles.py`

**Interfaces:**
- Produces: tables `users,roles,user_roles,param_definitions,param_profiles,param_profile_values,job_runs,audit_logs,system_settings`
- Produces: `POST /api/auth/login` → `{access_token}`；`CRUD /api/profiles`；种子含全部规格参数 + `rgb_preview` 预设

- [ ] **Step 1: 写 login + 创建 profile 测试**
- [ ] **Step 2: 实现 models/migration/seed（阶段参数中文说明来自规格 §5.2）**
- [ ] **Step 3: JWT 角色：admin/configurator/executor/viewer**
- [ ] **Step 4: pytest 通过；Commit**

---

### Task 3: Jobs API + 状态机 + MQ 发布

**Files:**
- Create: `backend/app/services/{jobs.py,mq.py}`
- Create: `backend/app/routers/jobs.py`
- Test: `backend/tests/test_job_state_machine.py`

**Interfaces:**
- Produces: `create_job(...) -> JobRun`；`transition(job, to_state)`；状态含 `awaiting_continue`
- Produces: `POST /api/jobs`, `/pause|/resume|/cancel|/continue`；发布到 `mosaic.jobs` / `mosaic.control`
- Consumes: profile snapshot + path validation against `DATA_ROOTS`

- [ ] **Step 1: 单测：full/until_stage/step 转换；同时仅一 running**
- [ ] **Step 2: 实现 jobs service + 路径校验（output 不在 input 内）**
- [ ] **Step 3: MQ publisher（pika）；无 broker 时测试用 fake**
- [ ] **Step 4: pytest；Commit**

---

### Task 4: source 阶段编排 + progress/control + CLI 扩展

**Files:**
- Create: `source/ms_mosaic/{progress.py,control.py,stage_runner.py}`
- Modify: `source/ms_mosaic/__main__.py`, `pipeline.py`（薄封装调用 stage_runner）
- Create: `source/docs/cli-usage.md`
- Test: `source/tests/test_stage_runner.py`

**Interfaces:**
- Produces: `run_stages(params: dict, reporter, control) -> dict` with keys `status` in `{succeeded, awaiting_continue, failed, cancelled}`
- Produces: CLI `--bands`, `--run-mode`, `--start-stage`, `--stop-after-stage`
- Consumes: existing `run_sparse` / dense / dsm / ortho 逻辑按阶段调用

- [ ] **Step 1: 单测 mock 各阶段：until_stage 在 S2 后 awaiting_continue；bands Color 只调 ortho Color**
- [ ] **Step 2: 实现 stage_runner 编排（真实调用现有函数）**
- [ ] **Step 3: CLI 与 cli-usage.md**
- [ ] **Step 4: pytest；Commit**

---

### Task 5: 宿主机 worker_main

**Files:**
- Create: `source/ms_mosaic/worker_main.py`
- Create: `source/scripts/run_worker.sh`
- Test: `source/tests/test_worker_handlers.py`（mock pika）

**Interfaces:**
- Consumes: MQ job message `{job_id, params_snapshot, run_attempt}`
- Produces: progress/events to `mosaic.progress` / `mosaic.events`；respect control pause/cancel/continue

- [ ] **Step 1–4: 实现消费循环 + 测试 + Commit**

---

### Task 6: Backend 进度消费 + WebSocket + 日志/报告下载

**Files:**
- Create: `backend/app/services/progress_ws.py`
- Modify: `backend/app/main.py`（startup consumer task）
- Create: `backend/app/routers/docs.py`（cli-guide 读 source/docs/cli-usage.md）
- Test: `backend/tests/test_ws_progress.py`

- [ ] **Step 1–4: 实现；Commit**

---

### Task 7: React 前端全页

**Files:**
- Create: `frontend/` Vite React TS Ant Design 工程与 Dockerfile
- Pages: Login, Profiles（阶段表单+仅RGB预设）, Execute（进度+继续）, Jobs, CliGuide, Admin
- Test: 至少 `frontend` build 通过；可选 vitest 烟雾

- [ ] **Step 1–4: 实现页面与 nginx 反代；`npm run build`；Commit**

---

### Task 8: README 联调说明 + 冒烟脚本

**Files:**
- Create: `visual_server/README.md`
- Create: `visual_server/scripts/smoke_api.sh`

- [ ] **Step 1: 文档：compose up、bootstrap admin、run_worker、仅 RGB 示例、阶段调试示例**
- [ ] **Step 2: Commit**

---

## Spec coverage checklist

| Spec 项 | Task |
| --- | --- |
| Compose PG/RQ/BE/FE | 1 |
| 参数字典/模板/角色 | 2 |
| Jobs/状态机/MQ | 3 |
| 仅 RGB / 阶段控制 / CLI | 4 |
| Worker 宿主机 | 5 |
| 进度 WS / 报告日志 / CLI 教学 API | 6 |
| React 全页含教学 | 7 |
| 运维 README | 8 |
