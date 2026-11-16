# Release Arch Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发版台每条版本构建记录必选并持久化 CPU 架构（`amd64`/`arm64`），按 `linux/{arch}` 构建全量离线镜像，tag 与交付包带架构后缀，同版本可并存多架构。

**Architecture:** `releases.arch` + 部分唯一索引 `(version, arch)`；`build_runner` 用 Docker `platform=` 构建/拉取；镜像 tag `{repo}:{version}-{arch}`；packager/compose 模板替换 `{{ARCH}}`；前端登记必选、列表展示、交付选版本带架构 label。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL 迁移、docker SDK 7.x、React + Ant Design、现有 release console。

## Global Constraints

- 架构取值仅 `amd64` | `arm64`；UI 展示 `linux/amd64`、`linux/arm64`。
- 登记时架构必选，无默认。
- 未删除记录上 `(version, arch)` 唯一。
- 编辑不可改 `arch`；需另一架构则新建记录。
- 镜像 tag：`{repo}:{version}-{arch}`；Docker platform：`linux/{arch}`。
- 旧行迁移回填 `arm64`。
- 不做多架构 manifest；不按发行版版本分别构建。

## File map

| File | Responsibility |
|------|----------------|
| `release/server/db/migrations/006_release_arch.sql` | 加列、回填、替换唯一索引 |
| `release/server/backend/app/bootstrap.py` | 注册跑 `006` |
| `release/server/backend/app/models.py` | `Release.arch` |
| `release/server/backend/app/schemas.py` | Create/Out 含 arch；校验枚举 |
| `release/server/backend/app/routers/releases.py` | 创建/更新按 `(version, arch)` |
| `release/server/backend/app/services/build_runner.py` | platform 构建、带 arch 的 tag |
| `release/server/backend/app/services/packager.py` | zip 名与模板 `{{ARCH}}` |
| `release/build/templates/customer-compose.yml` 等 | 默认镜像名含 `-{arch}` |
| `release/docs/customer-manual.md` (+ PDF) | 架构须匹配宿主机 |
| `release/server/frontend/src/pages/ReleasesPage.tsx` | 表单/列/确认文案 |
| `release/server/frontend/src/pages/DeliveriesPage.tsx` | 版本选项含架构 |
| `release/server/backend/tests/test_release_arch.py` | API 唯一性与校验 |
| `release/build/build_worker.sh` | 可选：接受 arch 参数（与 runner 一致） |

---

### Task 1: Migration + model + schemas

**Files:**
- Create: `release/server/db/migrations/006_release_arch.sql`
- Modify: `release/server/backend/app/bootstrap.py`（migrations 列表加入 `006_release_arch.sql`）
- Modify: `release/server/backend/app/models.py`（`Release.arch`）
- Modify: `release/server/backend/app/schemas.py`（`ReleaseCreateIn` / `ReleaseOut`）
- Create: `release/server/backend/tests/test_release_arch.py`（先写校验与常量测试；API 集成测可在 Task 2 补全）

**Interfaces:**
- Produces: `Release.arch: str`；`ALLOWED_ARCH = frozenset({"amd64", "arm64"})`；`ReleaseCreateIn(version, arch)`；`ReleaseOut.arch`

- [ ] **Step 1: Write migration SQL**

```sql
ALTER TABLE releases ADD COLUMN IF NOT EXISTS arch TEXT;
UPDATE releases SET arch = 'arm64' WHERE arch IS NULL OR arch = '';
ALTER TABLE releases ALTER COLUMN arch SET DEFAULT 'arm64';
ALTER TABLE releases ALTER COLUMN arch SET NOT NULL;

DROP INDEX IF EXISTS releases_version_alive_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS releases_version_arch_alive_uidx
  ON releases (version, arch) WHERE deleted_at IS NULL;
```

- [ ] **Step 2: Register migration in bootstrap**

In `bootstrap.py` migrations tuple, append `"006_release_arch.sql"` after `005_system_settings.sql`.

- [ ] **Step 3: Model + schemas**

```python
# models.py Release
arch: Mapped[str] = mapped_column(Text, default="arm64")

# schemas.py
ALLOWED_RELEASE_ARCH = frozenset({"amd64", "arm64"})

class ReleaseCreateIn(BaseModel):
    version: str = Field(min_length=1)
    arch: str = Field(min_length=1)

    @field_validator("arch")
    @classmethod
    def _arch_ok(cls, v: str) -> str:
        a = v.strip().lower()
        if a.startswith("linux/"):
            a = a.split("/", 1)[1]
        if a not in ALLOWED_RELEASE_ARCH:
            raise ValueError("arch 仅支持 amd64 或 arm64")
        return a

class ReleaseOut(BaseModel):
    # ...existing fields...
    arch: str
```

`ReleaseUpdateIn` 仍只有 `version`，不含 `arch`。

- [ ] **Step 4: Unit test for validator**

```python
# tests/test_release_arch.py
import pytest
from pydantic import ValidationError
from app.schemas import ReleaseCreateIn

def test_arch_normalizes_linux_prefix():
    r = ReleaseCreateIn(version="0.1.0", arch="linux/amd64")
    assert r.arch == "amd64"

def test_arch_rejects_unknown():
    with pytest.raises(ValidationError):
        ReleaseCreateIn(version="0.1.0", arch="ppc64")
```

- [ ] **Step 5: Run tests**

Run: `cd release/server/backend && python -m pytest tests/test_release_arch.py -v`  
（若在容器内：`docker compose exec backend pytest tests/test_release_arch.py -v`）  
Expected: PASS（需先把 schemas 改好）。

- [ ] **Step 6: Restart backend so migration applies**

```bash
cd release/server && docker compose up -d --build backend
docker compose logs backend --tail 30 | grep -i migration || true
```

Expected: 无致命错误；DB 中 `releases.arch` 存在。

- [ ] **Step 7: Commit**

```bash
git add release/server/db/migrations/006_release_arch.sql \
  release/server/backend/app/bootstrap.py \
  release/server/backend/app/models.py \
  release/server/backend/app/schemas.py \
  release/server/backend/tests/test_release_arch.py
git commit -m "feat(release): add releases.arch column and schema validation"
```

---

### Task 2: Releases API uniqueness on (version, arch)

**Files:**
- Modify: `release/server/backend/app/routers/releases.py`
- Modify: `release/server/backend/tests/test_release_arch.py`

**Interfaces:**
- Consumes: `ReleaseCreateIn.arch`、`Release.arch`
- Produces: create 写入 `arch`；冲突 detail `同版本同架构已存在`

- [ ] **Step 1: Update create_release**

```python
version = body.version.strip()
arch = body.arch  # already normalized
exists = db.scalar(
    select(Release).where(
        Release.version == version,
        Release.arch == arch,
        Release.deleted_at.is_(None),
    )
)
if exists:
    raise HTTPException(status_code=400, detail="同版本同架构已存在")
row = Release(version=version, arch=arch, status="pending")
```

- [ ] **Step 2: Update update_release uniqueness**

冲突检测改为同 `arch`：

```python
exists = db.scalar(
    select(Release).where(
        Release.version == version,
        Release.arch == row.arch,
        Release.id != release_id,
        Release.deleted_at.is_(None),
    )
)
if exists:
    raise HTTPException(status_code=400, detail="同版本同架构已存在")
```

- [ ] **Step 3: API-level test (TestClient or direct if现有 fixture；若无 fixture，用纯 SQLAlchemy session + 路由函数跳过，改为文档化手工验收)**

优先：若项目可起 TestClient，追加：

```python
def test_same_version_different_arch_allowed(...):
    # POST amd64 then arm64 same version -> both 200
def test_same_version_same_arch_rejected(...):
    # second POST -> 400
```

若集成测试成本过高：在本 Task 用 `docker compose exec` + curl 验收，并在测试文件保留 validator 单测即可。

- [ ] **Step 4: Manual curl check**

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8100/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST http://127.0.0.1:8100/api/releases -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"version":"arch-test-1","arch":"amd64"}'
curl -s -X POST http://127.0.0.1:8100/api/releases -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"version":"arch-test-1","arch":"arm64"}'
# second amd64 should 400
curl -s -X POST http://127.0.0.1:8100/api/releases -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"version":"arch-test-1","arch":"amd64"}'
```

- [ ] **Step 5: Commit**

```bash
git add release/server/backend/app/routers/releases.py release/server/backend/tests/test_release_arch.py
git commit -m "feat(release): enforce unique version+arch on create/update"
```

---

### Task 3: build_runner platform + arch tags

**Files:**
- Modify: `release/server/backend/app/services/build_runner.py`
- Modify: `release/build/build_worker.sh`（`VERSION` + `ARCH` 参数）

**Interfaces:**
- Consumes: `rel.arch`、`rel.version`
- Produces: tags `f"{repo}:{version}-{arch}"`；`platform=f"linux/{arch}"`；manifest 含 `arch=`

- [ ] **Step 1: Tag helper**

```python
def _image_tag(repo: str, version: str, arch: str) -> str:
    return f"{repo}:{version}-{arch}"
```

- [ ] **Step 2: Wire platform into build/pull/save**

在 `_run_build` 开头：

```python
arch = (rel.arch or "").strip().lower()
if arch not in {"amd64", "arm64"}:
    raise RuntimeError(f"非法 arch: {rel.arch!r}")
platform = f"linux/{arch}"
worker_tag = _image_tag(settings.worker_image_repo, rel.version, arch)
backend_tag = _image_tag(settings.backend_image_repo, rel.version, arch)
frontend_tag = _image_tag(settings.frontend_image_repo, rel.version, arch)
rel.image_name = worker_tag
```

`client.images.build(..., platform=platform, tag=...)`  
`client.api.pull(repo, tag=tag, platform=platform, stream=True, decode=True)`  
日志第一行：`platform={platform}\n`

跨架构失败时保持现有 `except` → `status=failed`，不要吞掉后改打本机架构。

- [ ] **Step 3: Update build_worker.sh**

```bash
VERSION="${1:?用法: $0 <version> <arch>}"
ARCH="${2:?用法: $0 <version> <arch>}"  # amd64|arm64
# IMAGE tags use ${VERSION}-${ARCH}
# docker build --platform linux/${ARCH} ...
```

- [ ] **Step 4: Smoke（可选短构建）**

对已有 release 触发 build 或仅确认代码导入：

```bash
docker compose exec backend python -c "from app.services.build_runner import _run_build; print('ok')"
```

正式全量 amd64 构建放到最终验收 Task。

- [ ] **Step 5: Commit**

```bash
git add release/server/backend/app/services/build_runner.py release/build/build_worker.sh
git commit -m "feat(release): build full stack for selected linux arch"
```

---

### Task 4: Packager + customer templates + manual

**Files:**
- Modify: `release/server/backend/app/services/packager.py`
- Modify: `release/build/templates/customer-compose.yml`
- Modify: `release/build/templates/customer.env.example`
- Modify: `release/build/templates/customer-README.md`
- Modify: `release/docs/customer-manual.md`
- Regenerate: `release/docs/customer-manual.pdf`

**Interfaces:**
- Consumes: `release.arch`、`release.version`、`release.image_name`
- Produces: zip `{customer}_{version}_{arch}_{license_id}.zip`；compose 默认 `spectrum-mosaic-*:{version}-{arch}`

- [ ] **Step 1: packager replacements**

```python
arch = release.arch or "arm64"
zip_path = out_dir / f"{customer.name}_{release.version}_{arch}_{license_row.license_id}.zip".replace("/", "_")
# in template replace:
.replace("{{ARCH}}", arch)
# IMAGE_NAME already from release.image_name (includes -{arch} after rebuild)
```

compose 模板中：

```yaml
image: ${BACKEND_IMAGE:-spectrum-mosaic-backend:{{VERSION}}-{{ARCH}}}
image: ${FRONTEND_IMAGE:-spectrum-mosaic-frontend:{{VERSION}}-{{ARCH}}}
```

worker 仍用 `{{IMAGE_NAME}}`。

- [ ] **Step 2: README / env / manual**

README 增加：「本包镜像架构为 `linux/{{ARCH}}`，须与宿主机一致」。  
手册「环境准备」与附录测试同样补一句。  
重新生成 PDF（沿用现有 `generate_pdf.py` + 中文字体方式）。

- [ ] **Step 3: Commit**

```bash
git add release/server/backend/app/services/packager.py \
  release/build/templates/ \
  release/docs/customer-manual.md release/docs/customer-manual.pdf
git commit -m "feat(release): include arch in delivery package and templates"
```

---

### Task 5: Frontend Releases + Deliveries

**Files:**
- Modify: `release/server/frontend/src/pages/ReleasesPage.tsx`
- Modify: `release/server/frontend/src/pages/DeliveriesPage.tsx`

**Interfaces:**
- Consumes: `Release.arch` from API
- Produces: UI 必选架构；列表列；交付 label `version (linux/arch)`

- [ ] **Step 1: ReleasesPage type + column + form**

```tsx
type Release = { /* ... */ arch: string };

const ARCH_OPTIONS = [
  { value: "amd64", label: "linux/amd64" },
  { value: "arm64", label: "linux/arm64" },
];

// columns after 版本:
{ title: "架构", dataIndex: "arch", width: 120, render: (a: string) => (a ? `linux/${a}` : "—") },

// create form:
<Form.Item name="arch" label="系统架构" rules={[{ required: true, message: "请选择架构" }]}>
  <Select placeholder="请选择架构" options={ARCH_OPTIONS} allowClear />
</Form.Item>
// POST body: { version, arch }
// edit: show Typography 只读 linux/{editing.arch}，不提交 arch
```

`openCreate`：`form.resetFields()` 且不设 arch 默认。  
`confirmBuild` 文案含 `linux/${row.arch}`。

- [ ] **Step 2: DeliveriesPage release options**

```tsx
label: `${r.version} (linux/${r.arch})`
```

确保 `Release` 类型含 `arch`。

- [ ] **Step 3: Rebuild frontend**

```bash
cd release/server && docker compose up -d --build frontend
```

- [ ] **Step 4: Commit**

```bash
git add release/server/frontend/src/pages/ReleasesPage.tsx \
  release/server/frontend/src/pages/DeliveriesPage.tsx
git commit -m "feat(release): UI for required release architecture"
```

---

### Task 6: End-to-end acceptance

**Files:** none（操作验收）

- [ ] **Step 1: Soft-delete 测试用 arch-test 记录（若 Task 2 创建了）**

- [ ] **Step 2: 登记 `0.1.0-rc2` + `amd64`，触发全量构建**

等待 `status=success`。日志含 `platform=linux/amd64`。

- [ ] **Step 3: 校验镜像架构**

```bash
docker image inspect spectrum-mosaic-worker:0.1.0-rc2-amd64 --format '{{.Architecture}}'
# Expected: amd64
```

- [ ] **Step 4: 同版本再登记 `arm64`（可选，证明唯一键）→ 仅登记成功即可，不必双构建**

- [ ] **Step 5: 用 amd64 release 签发许可并生成交付包**

检查 zip 名含 `-amd64-`；解压 `docker-compose.yml` 镜像 tag 含 `-amd64`。

- [ ] **Step 6: Final commit if any leftover docs/spec status**

若有文档小改：

```bash
git add docs/superpowers/specs/2026-11-14-release-arch-field-design.md
git commit -m "docs: mark release arch field design implemented"
```

---

## Spec coverage checklist

| Spec 项 | Task |
|---------|------|
| `arch` 列 + 回填 arm64 + 唯一索引 | Task 1 |
| Create 必填 / Update 不可改 arch | Task 1–2, 5 |
| `(version, arch)` 唯一与 400 | Task 2 |
| build `--platform` + tag 后缀 | Task 3 |
| packager/compose/zip/手册 | Task 4 |
| 前端必选/列表/交付 label | Task 5 |
| 验收标准 1–5 | Task 6 |

## Placeholder scan

无 TBD/TODO；关键代码与命令已内嵌。
