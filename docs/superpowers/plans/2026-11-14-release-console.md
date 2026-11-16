# 内部发版台（release）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在仓库根目录交付可用的内部发版台：PostgreSQL + FastAPI + 简单前端，支持版本构建登记、客户、离线 Ed25519 许可签发、交付 zip；客户 worker 镜像编译 `ms_mosaic` 并强制验签。

**Architecture:** `release/server` 只跑在我方；`release/build` 从 `mosaic/server` 打客户镜像；许可私钥在 `release/keys`；客户包不含 `release/`。worker 通过 `LICENSE_PATH` + 内置公钥离线验签。

**Tech Stack:** FastAPI、SQLAlchemy、psycopg、PostgreSQL 16、Vite+React、PyNaCl(Ed25519)、Cython、Docker Compose

## Global Constraints

- 第一期：内部发版台；完全离线许可；只校验签名与到期日；不用 SQLite
- 私钥永不进 git / 客户镜像 / 客户包
- 客户 compose 不挂 `./worker`、`./backend/app`
- 产品名固定：`spectrum-mosaic`
- 许可 JSON：`payload` + `signature`（base64）；canonical JSON 对 payload 签名
- 开发镜像可不强制许可；客户镜像 `MOSAIC_REQUIRE_LICENSE=1`

---

## File Structure

```text
release/
  README.md
  .gitignore
  keys/.gitkeep
  artifacts/.gitkeep
  server/
    docker-compose.yml
    .env.example
    README.md
    db/migrations/001_init.sql
    backend/          # FastAPI 发版台 API
    frontend/         # 四页管理 UI
  build/
    Dockerfile.worker-customer
    cython_modules.txt
    compile_in_image.sh
    templates/customer-compose.yml
    templates/customer-README.md
    templates/customer.env.example
mosaic/server/worker/ms_mosaic/license_check.py   # 验签（客户/开发共用）
```

---

### Task 1: release 脚手架 + PostgreSQL + 密钥初始化

**Files:**
- Create: `release/.gitignore`, `release/README.md`, `release/keys/.gitkeep`, `release/artifacts/.gitkeep`
- Create: `release/server/docker-compose.yml`, `release/server/.env.example`, `release/server/README.md`
- Create: `release/server/db/migrations/001_init.sql`
- Create: `release/server/backend/{Dockerfile,requirements.txt,app/...}`
- Modify: 根 `.gitignore` 增加 `release/keys/*`、`release/artifacts/**` 例外规则
- Test: `release/server/backend/tests/test_license_crypto.py`

**Interfaces:**
- Produces: `init_keys(keys_dir) -> Path` 公钥路径；`sign_license(payload, private_key) -> dict`；`verify_license(doc, public_key, now=None) -> None`（无效则抛 `LicenseError`）
- Produces: compose 服务 `postgres`（库名 `release_console`）、`backend`（端口 8100）、`frontend`（端口 8180）

- [ ] **Step 1: 写许可加解密单测（先失败）**

```python
# release/server/backend/tests/test_license_crypto.py
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.license_crypto import init_keys, sign_license, verify_license, LicenseError

def test_sign_and_verify_roundtrip(tmp_path: Path):
    priv, pub = init_keys(tmp_path)
    payload = {
        "license_id": "lic-1",
        "customer": "试用单位",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = sign_license(payload, priv)
    verify_license(doc, pub)

def test_expired_rejected(tmp_path: Path):
    priv, pub = init_keys(tmp_path)
    payload = {
        "license_id": "lic-2",
        "customer": "试用单位",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "expires_at": "2020-01-01T00:00:00Z",
        "issued_at": "2020-01-01T00:00:00Z",
    }
    doc = sign_license(payload, priv)
    try:
        verify_license(doc, pub)
        assert False, "expected LicenseError"
    except LicenseError as e:
        assert "过期" in str(e) or "expired" in str(e).lower()
```

- [ ] **Step 2: 实现 `license_crypto.py` + SQL 迁移 + backend 最小可启动 + compose**

许可文件格式固定：

```json
{
  "payload": {
    "license_id": "uuid",
    "customer": "string",
    "product": "spectrum-mosaic",
    "release_version": "0.1.0",
    "expires_at": "2026-12-31T23:59:59Z",
    "issued_at": "2026-11-14T00:00:00Z"
  },
  "signature": "<base64>"
}
```

`001_init.sql` 建表：`admins`, `customers`, `releases`, `licenses`, `deliveries`, `audit_logs`（字段对齐 design §5）。

Backend 环境变量：`DATABASE_URL`, `JWT_SECRET`, `KEYS_DIR`, `ARTIFACTS_DIR`, `MOSAIC_ROOT`（默认 `/mosaic` 挂载仓库的 `mosaic/server`）, `BOOTSTRAP_ADMIN_USER`, `BOOTSTRAP_ADMIN_PASSWORD`。

- [ ] **Step 3: 跑单测**

Run: `cd release/server/backend && pip install -r requirements.txt && pytest tests/test_license_crypto.py -v`  
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add release/ .gitignore docs/specs/2026-11-14-release-console-plan.md
git commit -m "feat(release): scaffold console with license crypto and postgres schema"
```

---

### Task 2: 认证 + 客户 / 版本 / 许可 / 交付 API

**Files:**
- Create: `release/server/backend/app/routers/{auth,customers,releases,licenses,deliveries}.py`
- Create: `release/server/backend/app/services/{audit,build_runner,packager}.py`
- Create: `release/server/backend/tests/test_api_customers.py`
- Modify: `release/server/backend/app/main.py`

**Interfaces:**
- `POST /api/auth/login` → `{access_token}`
- `CRUD /api/customers`
- `POST /api/releases` `{version}` → 创建 pending；`POST /api/releases/{id}/build` 触发构建线程
- `POST /api/licenses` `{customer_id, release_id, expires_at}` → 写 `.lic` 到 artifacts；`GET /api/licenses/{id}/download`
- `POST /api/deliveries` `{customer_id, release_id, license_id}` → zip；`GET /api/deliveries/{id}/download`
- 构建未 success 时 `POST /api/deliveries` 返回 400

- [ ] **Step 1: 写客户创建 API 测试并实现 routers**
- [ ] **Step 2: 实现签发（读 `KEYS_DIR` 私钥；缺失则 503）与交付打包（模板来自 `release/build/templates`）**
- [ ] **Step 3: pytest 相关用例 PASS 后 commit**

```bash
git commit -m "feat(release): add auth, customers, licenses, deliveries APIs"
```

---

### Task 3: 发版台前端四页

**Files:**
- Create: `release/server/frontend/`（Vite React TS，端口开发 5174，生产 nginx 8180）
- Pages: Login, Releases, Customers, Licenses, Deliveries

**Interfaces:**
- 调用 Task 2 的 API；JWT 存 localStorage；中文简体文案

- [ ] **Step 1: 脚手架 + 登录页 + 布局导航**
- [ ] **Step 2: 四个业务页（列表 / 表单 / 下载按钮）**
- [ ] **Step 3: Dockerfile 多阶段构建静态资源；compose 接通**
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(release): add admin UI for releases customers licenses deliveries"
```

---

### Task 4: 客户 worker 镜像构建（Cython）+ 构建编排

**Files:**
- Create: `release/build/Dockerfile.worker-customer`
- Create: `release/build/compile_in_image.sh`
- Create: `release/build/cython_modules.txt`（列出需编译的 `ms_mosaic` 模块；入口 `worker_main`/`job_child`/`__main__`/`license_check` 可保留极薄 py 或同样编译后留 loader）
- Create: `release/build/templates/{customer-compose.yml,customer-README.md,customer.env.example}`
- Modify: `release/server/backend/app/services/build_runner.py` 调用宿主机 `docker build`（backend 挂载 docker.sock）或文档化为外部脚本 + API 只登记结果；**本期采用：backend 容器挂载 `/var/run/docker.sock` + 仓库路径，执行 build 脚本**

**Interfaces:**
- `build_runner.start_build(release_id) -> None`：状态 running→success/failed；写 `artifacts/releases/<id>/build.log`、`image.tar`（可选）、digest
- 镜像标签：`spectrum-mosaic-worker:<version>`
- `ARG EMBED_PUBLIC_KEY` 构建时写入镜像 `/app/license_public.pem`
- `ENV MOSAIC_REQUIRE_LICENSE=1`

- [ ] **Step 1: 写 Dockerfile.worker-customer 多阶段（python:3.11 + Cython + 编译 + 删 .py）**
- [ ] **Step 2: 本地 `docker build` 冒烟（可先只编译子集若全量过慢，但验收需核心算法 .py 不在镜像内）**
- [ ] **Step 3: 接通 `POST /api/releases/{id}/build`**
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(release): customer worker image build with Cython and license pubkey"
```

---

### Task 5: mosaic worker 离线验签钩子

**Files:**
- Create: `mosaic/server/worker/ms_mosaic/license_check.py`
- Create: `mosaic/server/worker/tests/test_license_check.py`
- Modify: `mosaic/server/worker/ms_mosaic/worker_main.py`（spawn 任务前验签）
- Modify: `mosaic/server/worker/ms_mosaic/job_child.py`（子进程入口再验一次）

**Interfaces:**
- `enforce_license_or_raise()`：若 `MOSAIC_REQUIRE_LICENSE` 非真则直接返回；否则读 `LICENSE_PATH`（默认 `/license/license.lic`）与 `LICENSE_PUBLIC_KEY_PATH`（默认 `/app/license_public.pem`）
- 失败日志：`许可无效或已过期`

- [ ] **Step 1: 单测验签 / 过期 / 缺省跳过**
- [ ] **Step 2: 钩入 worker_main / job_child**
- [ ] **Step 3: pytest PASS + commit**

```bash
git commit -m "feat(mosaic): enforce offline license check in customer worker"
```

---

### Task 6: 端到端验收与文档

**Files:**
- Modify: `release/README.md`, `release/server/README.md`, 根 `README.md`（增加 release 入口一句）
- Modify: design spec 状态改为「已实现（第一期）」

- [ ] **Step 1: `docker compose up` release；登录；建客户；初始化密钥；签发；下载 lic**
- [ ] **Step 2: 构建客户 worker 镜像；抽检无核心 `.py`；过期 lic 启动应失败**
- [ ] **Step 3: 生成交付 zip 结构符合 design §7.2**
- [ ] **Step 4: Commit 文档**

```bash
git commit -m "docs(release): usage and migration notes for console volumes"
```

---

## Spec coverage

| Spec 项 | Task |
| --- | --- |
| release/ 目录与 PG Docker | 1 |
| 四页 + 客户/版本/许可/交付 | 2–3 |
| Cython 客户镜像 | 4 |
| 离线验签 | 5 |
| 交付 zip / 验收 | 6 |
| 不做自助申请/绑机/在线吊销 | 全局约束，无对应功能 |
