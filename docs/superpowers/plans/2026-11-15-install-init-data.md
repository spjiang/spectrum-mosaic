# Install init.sql + init-data.sql Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新装 mosaic 时用固定的 `db/init.sql`（结构）与 `db/init-data.sql`（全部初始业务数据）完成库初始化，并自动建好试用账号数据目录。

**Architecture:** 权威结构文件 `mosaic/server/db/init.sql`（纯 DDL）；权威种子 `mosaic/server/db/init-data.sql`（角色/用户/RBAC/两条默认方案，全部幂等）。`bootstrap()` 空库跑 `init.sql`，结构就绪后跑 `init-data.sql`，再用 Python 仅 `mkdir` 试用目录。存量库继续跑 `migrations/*.sql` 升级列。

**Tech Stack:** PostgreSQL 16、FastAPI bootstrap、SQLAlchemy `text()`、bcrypt（passlib）预计算哈希、pytest。

## Global Constraints

- 结构只进 `mosaic/server/db/init.sql`；业务种子只进 `mosaic/server/db/init-data.sql`。
- 试用账号：`trial1` / `trial2`，密码 `trial@123456`，角色 `executor`，期限首次插入起 30 天，已存在不刷新。
- 默认方案名固定：`标准全波段`、`RGB 快速预览`；`created_by IS NULL`；方案内目录不绑 trial 用户名。
- 不自动创建 `zdrh`；不导入测区样例数据。
- `init-data.sql` 可重复执行且不翻倍、不续期。
- mkdir 失败只打 warning，不阻断启动。

## File map

| File | Responsibility |
| --- | --- |
| `mosaic/server/db/init.sql` | 新装 DDL（由 `001_init.sql` 演进：去掉角色 INSERT，users 含 `expires_at`） |
| `mosaic/server/db/init-data.sql` | 全部初始 DML：角色、用户、角色关联、RBAC、两方案+参数值 |
| `mosaic/server/db/migrations/001_init.sql` | 保留给旧路径/注释指向 `../init.sql`；或薄包装避免双源（见 Task 1） |
| `mosaic/server/backend/app/bootstrap.py` | 空库跑 init.sql；跑 init-data.sql；mkdir；去掉 `_ensure_demo_users` ORM 插用户 |
| `mosaic/server/docker-compose.yml` | 挂载整个 `./db`；postgres initdb 用 `init.sql` |
| `mosaic/server/backend/tests/test_init_data_sql.py` | 幂等与种子内容断言（可用 SQL 解析或临时 PG） |

---

### Task 1: 新增 `db/init.sql`（纯结构）

**Files:**
- Create: `mosaic/server/db/init.sql`
- Modify: `mosaic/server/db/migrations/001_init.sql`（文件头注释：结构权威为 `../init.sql`；内容改为 `\i` 不可用时改为复制说明或 `INCLUDE` 文档——PostgreSQL 无 include，故 **001 保留与 init.sql 同步的 DDL 副本仅用于 docker initdb 兼容，或 docker 改挂 init.sql**）
- Modify: `mosaic/server/docker-compose.yml`（postgres 卷改为 `./db/init.sql:/docker-entrypoint-initdb.d/001_init.sql:ro`；backend 增加 `./db:/db:ro`）

**Interfaces:**
- Produces: 空库执行后存在 `users`（含 `expires_at TIMESTAMPTZ NULL`）、`roles`、`user_roles`、`param_definitions`、`param_profiles`、`param_profile_values`、`job_runs`、`audit_logs`、`system_settings`；**无业务 INSERT**。

- [ ] **Step 1: 写 `init.sql`**

从 `migrations/001_init.sql` 复制 DDL，做两处修改：
1. `users` 表增加 `expires_at TIMESTAMPTZ`（与 `026_user_expires_at.sql` 对齐，新装一步到位）。
2. **删除** 文件末尾 `INSERT INTO roles ...`（角色改由 `init-data.sql` 插入）。

- [ ] **Step 2: 改 compose 挂载**

`postgres.volumes`：
```yaml
- ./db/init.sql:/docker-entrypoint-initdb.d/001_init.sql:ro
```
`backend.volumes` 增加（保留 migrations 挂载或合并为整目录）：
```yaml
- ./db:/db:ro
```

- [ ] **Step 3: `001_init.sql` 顶部加注释**

```sql
-- DEPRECATED as schema source of truth.
-- Canonical schema: ../init.sql
-- Kept temporarily for older docs; prefer mounting ../init.sql in compose.
```

- [ ] **Step 4: Commit**

```bash
git add mosaic/server/db/init.sql mosaic/server/db/migrations/001_init.sql mosaic/server/docker-compose.yml
git commit -m "feat(db): add canonical init.sql schema for mosaic installs"
```

---

### Task 2: 编写 `db/init-data.sql`（全部初始数据）

**Files:**
- Create: `mosaic/server/db/init-data.sql`
- Test: `mosaic/server/backend/tests/test_init_data_sql.py`（先测文件存在与关键 SQL 片段；有 PG 时再跑幂等）

**Interfaces:**
- Consumes: Task 1 表结构 + 已有 `param_definitions`（bootstrap 仍会先 ensure `002_param_definitions.sql` 等，**再**执行 init-data；方案 values 只引用已存在的 param keys）
- Produces: 幂等种子：4 角色、5+ 用户（admin/executor/configurator/viewer/trial1/trial2）、rbac_menus、两方案及 values

**预计算 bcrypt（passlib bcrypt，与 `app.auth.hash_password` 一致；实现时若校验失败可重新生成并写入本文件）：**

| 明文 | 哈希（写入 SQL） |
| --- | --- |
| `admin123` | `$2b$12$4JQ6OqbwGSu0QLY1Isr9c.8tjbWMUUo5jQmdyNJ6WGaEtNpN5xis2` |
| `executor123` | `$2b$12$L08M0D3UrOIkyCSaO4ykrOkABV3kThSpsHB3uzctM2QA1h9E91cDa` |
| `configurator123` | `$2b$12$IMTpGzqz8.EzRmSpZoPPWuEpCzb7IVnF1EjMh2ReQjfRwcrSEZOs.` |
| `viewer123` | `$2b$12$u35u4AveS425Q.7k8zGOROug7rP8IyUdzK0XgATfBoP9pHzczlA3a` |
| `trial@123456` | `$2b$12$Ffa2qPD7Uokdus3pvtjbu.DBtFhVpG5GUFmpcEpIluo39NIEOgsrm` |

- [ ] **Step 1: 写失败测试（文件与关键标记）**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "db"  # backend/../db — adjust if needed
# Prefer: parents[3]/server/db when tests live in backend/tests
DB = Path(__file__).resolve().parents[2].parent / "db"


def test_init_data_sql_exists_and_has_profiles():
    text = (DB / "init-data.sql").read_text(encoding="utf-8")
    assert "标准全波段" in text
    assert "RGB 快速预览" in text
    assert "trial1" in text and "trial2" in text
    assert "rbac_menus" in text
    assert "ON CONFLICT" in text or "WHERE NOT EXISTS" in text
```

路径：`mosaic/server/backend/tests` → parents[1]=backend, parents[2]=server → `server/db`。用：

```python
DB = Path(__file__).resolve().parents[2] / "db"
```

- [ ] **Step 2: Run test — expect FAIL（文件不存在）**

Run: `cd mosaic/server/backend && python -m pytest tests/test_init_data_sql.py::test_init_data_sql_exists_and_has_profiles -v`  
Expected: FAIL FileNotFoundError / assert

- [ ] **Step 3: 写 `init-data.sql` 正文**

按顺序（注释分段）：

**A. roles**
```sql
INSERT INTO roles (name) VALUES
  ('admin'), ('configurator'), ('executor'), ('viewer')
ON CONFLICT (name) DO NOTHING;
```

**B. users**（无 expires 的账号 `expires_at` 为 NULL；trial 用日末）

```sql
INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'admin', '$2b$12$4JQ6OqbwGSu0QLY1Isr9c.8tjbWMUUo5jQmdyNJ6WGaEtNpN5xis2', TRUE, NULL
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');

-- executor / configurator / viewer 同理（哈希见上表）

INSERT INTO users (username, password_hash, is_active, expires_at)
SELECT 'trial1', '$2b$12$Ffa2qPD7Uokdus3pvtjbu.DBtFhVpG5GUFmpcEpIluo39NIEOgsrm', TRUE,
       (((CURRENT_DATE + 30)::timestamp + interval '1 day' - interval '1 millisecond') AT TIME ZONE 'Asia/Shanghai')
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'trial1');

-- trial2 同理
```

**C. user_roles**（按 username→role name，WHERE NOT EXISTS）

**D. rbac_menus**（仅 key 不存在时插入；JSON 用现行 DEFAULT_MENUS）

```sql
INSERT INTO system_settings (key, value)
SELECT 'rbac_menus', '{
  "admin": ["execute","incremental","profiles","jobs","seam","worker","health","settings","inspect","cli","users","roles","permissions","audit"],
  "configurator": ["profiles","jobs","seam","settings","inspect","cli"],
  "executor": ["execute","incremental","profiles","jobs","seam","inspect","cli"],
  "viewer": ["profiles","jobs","seam","inspect","cli"]
}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE key = 'rbac_menus');
```

**E. profiles**（按 name 不存在则插入；`created_by` NULL）

```sql
INSERT INTO param_profiles (name, description, version, preset, created_by)
SELECT '标准全波段',
       '正式生产默认: 8 波段全图, 步进扫描（不包含窄带扫描）',
       1, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM param_profiles WHERE name = '标准全波段');

INSERT INTO param_profiles (name, description, version, preset, created_by)
SELECT 'RGB 快速预览',
       '仅 Color 正射, 快速查看几何与拼接',
       1, 'rgb_preview', NULL
WHERE NOT EXISTS (SELECT 1 FROM param_profiles WHERE name = 'RGB 快速预览');
```

**F. param_profile_values**  
对每个方案，用 `INSERT … SELECT … WHERE NOT EXISTS (profile_id, param_key)` 写入核心键。取值对齐现网 #1/#2，但：

- `input_dir` → `null::jsonb`
- `output_dir` → `null::jsonb`
- 标准全波段：`bands` 为 8 波段数组；`preset` null；`match_reference_color` false；`run_mode` `"full"` 等
- RGB：`bands`/`at_bands` 为 `["Color"]`；`preset` `"rgb_preview"`；`run_mode` `"full"`

完整键列表（两方案相同骨架，bands/preset/at_bands 不同）至少包含现网 dump 中的：  
`bands, benchmark_dir, cache_dir, calibrate_intrinsics, color_correction, drop_white_panel, dsm_gsd, input_dir, log_dir, match_reference_color, max_fill_gap_m, max_frames, max_index, max_tilt_deg, min_agl_m, n_layers, outlier_threshold_px, output_dir, preset, primary_band, process_dir, products_dir_name, require_pos, reuse_dsm, run_mode, seamline_enabled, sigma_attitude_deg, sigma_xy_m, sigma_z_m, spike_tolerance_m, start_stage, stop_after_stage, workers_at, workers_dense, workers_ortho, write_json_report, write_pdf_report, z_margin_m`  
以及 RGB 的 `at_bands`（若字典已有该 key；无则跳过该行——INSERT 前 `AND EXISTS (SELECT 1 FROM param_definitions WHERE key=...)`）。

示例单行模式：
```sql
INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, 'bands', '["Color","450nm","550nm","650nm","720nm","750nm","800nm","850nm"]'::jsonb
FROM param_profiles p
WHERE p.name = '标准全波段'
  AND EXISTS (SELECT 1 FROM param_definitions d WHERE d.key = 'bands')
  AND NOT EXISTS (
    SELECT 1 FROM param_profile_values v
    WHERE v.profile_id = p.id AND v.param_key = 'bands'
  );
```

- [ ] **Step 4: Run test — expect PASS**

Run: `python -m pytest tests/test_init_data_sql.py::test_init_data_sql_exists_and_has_profiles -v`

- [ ] **Step 5: Commit**

```bash
git add mosaic/server/db/init-data.sql mosaic/server/backend/tests/test_init_data_sql.py
git commit -m "feat(db): add init-data.sql with default profiles and trial users"
```

---

### Task 3: bootstrap 接入 init.sql / init-data.sql + mkdir

**Files:**
- Modify: `mosaic/server/backend/app/bootstrap.py`
- Test: `mosaic/server/backend/tests/test_bootstrap_init_data.py`（可 mock Session + tmp SQL，或标记 integration）

**Interfaces:**
- Consumes: `/db/init.sql`、`/db/init-data.sql`（及开发态 `server/db/`）
- Produces: `_db_dir() -> Path`；`_ensure_init_data(db)`；`_ensure_trial_dirs()`；不再调用 `_ensure_demo_users`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
from app.services.paths import trial_dirs

def test_trial_dirs_for_seed_users():
    assert trial_dirs("trial1") == ("/data/input/trial1", "/data/output/runs/trial1")
    assert trial_dirs("trial2") == ("/data/input/trial2", "/data/output/runs/trial2")
```

另测 `_db_dir` 能解析到含 `init-data.sql` 的目录（若将该函数从 bootstrap 导出或放到小模块 `app.db_paths`）。

- [ ] **Step 2: Run — paths 测试应 PASS（已有 trial_dirs）；若测 `_ensure_init_data` 未实现则 FAIL**

- [ ] **Step 3: 实现 bootstrap 变更**

1. 增加 `_db_dir() -> Path`：候选 `here.parents[2]/db`、`Path("/db")`、`here.parents[1]/db`。
2. 空库时：`_run_sql_file(db, _db_dir() / "init.sql")`，缺文件则回退 `migrations/001_init.sql` 并打 warning。
3. 在 `_ensure_user_expires_at` 与参数字典 ensure **之后**，调用：
   ```python
   data = _db_dir() / "init-data.sql"
   if not data.is_file():
       raise RuntimeError(f"缺少初始数据文件: {data}")
   _run_sql_file(db, data)
   ```
4. `_ensure_trial_dirs()`：
   ```python
   for username in ("trial1", "trial2"):
       for root in trial_dirs(username):
           try:
               Path(root).mkdir(parents=True, exist_ok=True)
           except OSError as exc:
               log.warning("mkdir %s failed: %s", root, exc)
   ```
5. **删除** `_ensure_demo_users` 及其调用（改由 SQL）。
6. admin 兜底：若 SQL 已插入 admin 则现有 `if user is None` 不触发；保留兜底以兼容自定义 `BOOTSTRAP_ADMIN_*`（若用户名不是 admin 且不存在则仍创建）。

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_init_data_sql.py tests/test_job_paths.py tests/test_user_expiry.py -q`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mosaic/server/backend/app/bootstrap.py mosaic/server/backend/tests/test_bootstrap_init_data.py
git commit -m "feat(bootstrap): apply init-data.sql and ensure trial directories"
```

---

### Task 4: 本地验证（空库或重建）

**Files:** none（验证）

- [ ] **Step 1: 重建栈（会清空本地 PG 卷时先确认）**

```bash
cd mosaic/server
docker compose up -d --build backend postgres
```

若需干净库：`docker compose down` 后删 `pgdata` 卷再 up（**仅在确认可丢本地库时**）。

- [ ] **Step 2: 查库**

```bash
docker exec server-postgres-1 psql -U mosaic -d mosaic_visual -c \
  "SELECT username, expires_at IS NOT NULL AS trial FROM users ORDER BY id;"
docker exec server-postgres-1 psql -U mosaic -d mosaic_visual -c \
  "SELECT id, name, preset FROM param_profiles ORDER BY id;"
```

Expected: 含 trial1/trial2；含「标准全波段」「RGB 快速预览」。

- [ ] **Step 3: 查目录**

```bash
docker exec server-backend-1 ls -la /data/input /data/output/runs | head
```

Expected: 存在 `trial1`、`trial2` 子目录。

- [ ] **Step 4: 二次 restart 后行数不变**

```bash
docker compose restart backend
# 再查 users / param_profiles 行数
```

- [ ] **Step 5: Commit 若有 compose/文档微调**

```bash
git add -u mosaic/server
git commit -m "chore: verify install seed wiring"
```

（无变更可跳过 commit）

---

## Spec coverage self-check

| Spec 要求 | Task |
| --- | --- |
| `init.sql` = 结构 | Task 1 |
| `init-data.sql` = 全部初始数据 | Task 2 |
| trial1/trial2 + 30 天 + executor | Task 2 |
| 两默认方案 created_by null | Task 2 |
| bootstrap 跑 SQL + mkdir | Task 3 |
| 幂等不续期 | Task 2 SQL + Task 4 |
| 不建 zdrh / 不导入测区 | 全局未包含 |
| compose 可找到文件 | Task 1 挂载 |

## Placeholder scan

无 TBD；哈希已写入计划表；方案 values 用明确 INSERT 模式与键列表。
