# 推扫拼图工作台与安装配置 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「新建推扫」「推扫记录」合成以架次为中心的「推扫拼图」工作台，把「相机标定」改成支持多组、按飞行日期生效的「安装配置」，标定改为后台运行，顺带修掉走查发现的 7 个问题。

**Architecture:** 后端新增 `pushbroom_mounts` 服务（选配置规则、增删改、手动指定、旧标定迁移），`pushbroom_calibration` 改成「建记录 → 后台线程调 RPC → 写回」，`/flights` 返回每个架次对应的配置和最近任务。前端新建 `pages/pushbroom/workbench/` 和 `pages/pushbroom/mounts/` 两组组件，删掉旧的三个页面，菜单键迁移到 `pushbroom_workbench`、`pushbroom_mounts`。

**Tech Stack:** FastAPI、SQLAlchemy 2、PostgreSQL（启动时跑 `db/migrations/*.sql`）、pika、PyJWT；React 18、antd 5.29、react-router 6、Vite。

**Spec:** `docs/superpowers/specs/2026-11-21-pushbroom-workbench-design.md`

## Global Constraints

- 代码注释一律中文；页面文案中文。
- 不用过时的 API：antd 用 `Flex` 代替 `Space direction="vertical"`，`Modal` 用 `destroyOnHidden`，`InputNumber` 用 `suffix`，不用 `Card bordered`。
- 安装参数值只来自标定结果，接口不收前端传来的数字；任务里出现 `along_offset_m`、`boresight_px`、`mount_profile` 一律 400。
- 已提交的任务不受配置修改影响；重跑沿用原 `params`。
- 内置值：沿航向 1.70 m，狭缝中心 −19 像元。
- 时间显示一律北京时间（UTC+8）；飞行日期取 GPS 第一条 `UnixTime` 对应的北京时间日期。
- 标定结果 30 分钟内有效；同一时间只允许一次标定。
- 只在本地分支 `feature/pushbroom-mosaic` 提交，不推送。提交人 Climb / spjiang@aliyun.com。
- 本机 git 是 2.24，`git commit` 会报 `unknown option 'trailer'`。每次提交用：
  ```bash
  git add <文件…> && tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '<说明>')"
  ```
  然后 `git status --short` 确认只提交了这些文件。
- 不要提交 `客户/`、`mosaic/server/pushbroom-worker/docs/`、`.superpowers/`、`release/build/templates/uninstall.ps1`。
- Docker 一律带两个 compose 文件：`docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml …`（在 `mosaic/server` 下）。
- 不删任务 #9–#17，不提交 0410 架次，不重跑全部生产架次。
- 后端测试命令（在 `mosaic/server/backend` 下）：`PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_*.py tests/test_eta_and_menus.py`。其它测试文件缺 numpy，收集失败是原有问题，不管。
- 前端检查命令（在 `mosaic/server/frontend` 下）：`npx tsc --noEmit -p tsconfig.json && npm run build`。

## 开工前

工作区里有别人未提交的前端改动：`components/ImageStage.tsx`（新文件）、`OrthoStage.tsx`、`JobRgbThumbnail.tsx`、`PushbroomJobs.tsx`、`theme.css`。Task 9 的结果预览要用 `ImageStage.tsx` 导出的 `ImageInspectModal`。开工前先问用户这些改动是否保留、是否先由用户提交。不要替用户提交或回滚这些文件。

## 文件结构

后端（`mosaic/server/`）：

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `db/migrations/031_pushbroom_mounts.sql` | 新建 | 三张新表 |
| `backend/app/models.py` | 修改 | 三个新模型 |
| `backend/app/services/pushbroom_check.py` | 修改 | 返回 `started_at` |
| `backend/app/services/pushbroom_mounts.py` | 新建 | 选配置规则、抄进任务、增删改、手动指定、迁移 |
| `backend/app/services/pushbroom_calibration.py` | 重写 | 后台标定、运行凭证 |
| `backend/app/services/pushbroom.py` | 修改 | 建任务用安装配置、架次统计、排队位置、日志北京时间 |
| `backend/app/routers/pushbroom.py` | 修改 | 新接口、删旧标定接口 |
| `backend/app/services/rbac.py` | 修改 | 菜单键迁移 |
| `backend/app/bootstrap.py` | 修改 | 跑 031、迁移旧标定、收口中断的标定 |
| `backend/tests/test_pushbroom_mounts.py` | 新建 | |
| `backend/tests/test_pushbroom_calibration.py` | 重写 | |
| `backend/tests/test_pushbroom_jobs.py`、`test_pushbroom_check.py` | 修改 | |

前端（`mosaic/server/frontend/src/`）：

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `api.ts` | 修改 | 新类型和接口，删旧标定接口 |
| `pages/pushbroom/labels.ts` | 修改 | 配置、时间、数值的显示 |
| `pages/pushbroom/MountTag.tsx` | 新建 | 配置标签 |
| `pages/pushbroom/ProductHelp.tsx` | 新建 | 成果文件说明（从旧记录页搬出） |
| `pages/pushbroom/usePreviewUrl.ts` | 新建 | 带登录头取预览图 |
| `pages/pushbroom/workbench/*.tsx` | 新建 | 工作台：页面、架次列表、顶部、主区域、历次任务、页签、任务操作 |
| `pages/pushbroom/mounts/*.tsx` | 新建 | 安装配置页：页面、配置表、架次对应表、新建配置 |
| `pages/pushbroom/PushbroomNew.tsx`、`PushbroomJobs.tsx`、`PushbroomCalibration.tsx` | 删除 | |
| `roles.ts`、`components/AppShell.tsx`、`rbac.tsx` | 修改 | 菜单、路由、旧地址跳转、开关缓存 |
| `theme.css` | 修改 | 工作台样式 |

---

### Task 1: 核对时带出飞行开始时间

**Files:**
- Modify: `mosaic/server/backend/app/services/pushbroom_check.py:164-191`
- Test: `mosaic/server/backend/tests/test_pushbroom_check.py`

**Interfaces:**
- Produces: `quick_check(dir).flight["started_at"]`：GPS 第一条记录的 UTC ISO 字符串（`datetime.isoformat()`，带 `+00:00`），`UnixTime <= 0` 时为 `None`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_pushbroom_check.py` 的 `test_good_flight_passes_and_reports_layout` 后面加：

```python
def test_flight_start_comes_from_first_gps_record(tmp_path):
    result = quick_check(_flight(tmp_path))
    # 第一条定位 UnixTime = 100
    assert result.flight["started_at"] == "1970-01-01T00:01:40+00:00"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd mosaic/server/backend && PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_check.py -k flight_start`
Expected: FAIL，`KeyError: 'started_at'`

- [ ] **Step 3: 实现**

`pushbroom_check.py` 顶部 import 改为：

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

`_check_gps` 末尾一行改为：

```python
    started = datetime.fromtimestamp(t0, timezone.utc).isoformat() if t0 > 0 else None
    result.flight.update(gps_records=len(records), duration_s=max(0, t1 - t0), started_at=started)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_check.py`
Expected: 全部 PASS（真实架次的用例在本机可能 skip）

- [ ] **Step 5: 提交**

```bash
git add mosaic/server/backend/app/services/pushbroom_check.py mosaic/server/backend/tests/test_pushbroom_check.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：核对架次时带出飞行开始时间。')"
```

---

### Task 2: 安装配置的表、模型和选配置规则

**Files:**
- Create: `mosaic/server/db/migrations/031_pushbroom_mounts.sql`
- Modify: `mosaic/server/backend/app/models.py`（import 行、`PushbroomCalibration` 后面）
- Create: `mosaic/server/backend/app/services/pushbroom_mounts.py`
- Test: `mosaic/server/backend/tests/test_pushbroom_mounts.py`

**Interfaces:**
- Produces（`app.services.pushbroom_mounts`）：
  - `CST: timezone`（UTC+8）
  - `BUILTIN: dict[str, float]` = `{"along_offset_m": 1.7, "boresight_px": -19.0}`
  - `@dataclass(frozen=True) class Resolved: source: str; profile: Any | None = None`，`source` 取 `"manual" | "date" | "builtin"`
  - `flight_date(started_at: str | None) -> date | None`
  - `resolve(profiles: Iterable, override_id: uuid.UUID | None, day: date | None) -> Resolved`
  - `mount_out(res: Resolved) -> dict`：`{"id", "name", "source", "along_offset_m", "boresight_px"}`
  - `apply_mount(params: dict, res: Resolved) -> dict`
  - `count_matches(profiles, overrides: dict[str, uuid.UUID], flights: list[tuple[str, str | None]]) -> dict[str, int]`
  - `load_profiles(db) -> list[PushbroomMountProfile]`（按生效日期升序）
  - `load_overrides(db) -> dict[str, uuid.UUID]`
  - `resolve_for(db, flight_name: str, started_at: str | None) -> Resolved`
- Produces（`app.models`）：`PushbroomMountProfile`、`PushbroomFlightProfile`、`PushbroomCalibrationRun`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_pushbroom_mounts.py`：

```python
"""安装配置：按飞行日期选配置、手动指定优先、内置兜底、抄进任务。"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import pushbroom_mounts as mounts


def _profile(name, day, along=1.78, bore=-49.8):
    return SimpleNamespace(id=uuid.uuid4(), name=name, effective_from=day, along_offset_m=along, boresight_px=bore)


P0414 = _profile("0414 安装", date(2026, 4, 14))
P0419 = _profile("0419 安装", date(2026, 4, 19), along=1.3, bore=-30.0)
PROFILES = [P0414, P0419]


def test_flight_date_is_beijing_date():
    # 2026-04-13 17:00 UTC 是北京时间 4 月 14 日 01:00
    assert mounts.flight_date("2026-04-13T17:00:00+00:00") == date(2026, 4, 14)
    assert mounts.flight_date("2026-04-13T15:00:00+00:00") == date(2026, 4, 13)
    assert mounts.flight_date(None) is None
    assert mounts.flight_date("不是时间") is None


def test_latest_profile_not_after_flight_date_wins():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 18)) == mounts.Resolved("date", P0414)
    assert mounts.resolve(PROFILES, None, date(2026, 4, 29)) == mounts.Resolved("date", P0419)


def test_effective_day_itself_counts():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 19)).profile is P0419


def test_flight_before_every_profile_uses_builtin():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 10)) == mounts.Resolved("builtin")
    assert mounts.resolve(PROFILES, None, None) == mounts.Resolved("builtin")
    assert mounts.resolve([], None, date(2026, 4, 29)) == mounts.Resolved("builtin")


def test_manual_override_beats_date():
    assert mounts.resolve(PROFILES, P0414.id, date(2026, 4, 29)) == mounts.Resolved("manual", P0414)
    # 指向已删除的配置时按日期走
    assert mounts.resolve(PROFILES, uuid.uuid4(), date(2026, 4, 29)).profile is P0419


def test_mount_out_shapes():
    assert mounts.mount_out(mounts.Resolved("builtin")) == {
        "id": None,
        "name": None,
        "source": "builtin",
        "along_offset_m": 1.7,
        "boresight_px": -19.0,
    }
    out = mounts.mount_out(mounts.Resolved("date", P0414))
    assert out == {"id": str(P0414.id), "name": "0414 安装", "source": "date", "along_offset_m": 1.78, "boresight_px": -49.8}


def test_apply_mount_copies_values_and_name():
    params = mounts.apply_mount({"bands": [1, 2]}, mounts.Resolved("manual", P0414))
    assert params == {
        "bands": [1, 2],
        "along_offset_m": 1.78,
        "boresight_px": -49.8,
        "mount_profile": {"id": str(P0414.id), "name": "0414 安装", "source": "manual"},
    }


def test_apply_mount_builtin_leaves_values_to_worker():
    params = mounts.apply_mount({"gsd_m": 0.04}, mounts.Resolved("builtin"))
    assert params == {"gsd_m": 0.04, "mount_profile": {"source": "builtin"}}


@pytest.mark.parametrize("key", ["along_offset_m", "boresight_px", "mount_profile"])
def test_job_cannot_carry_mount_fields(key):
    with pytest.raises(HTTPException) as exc:
        mounts.apply_mount({key: 1}, mounts.Resolved("builtin"))
    assert exc.value.status_code == 400
    assert "不能在任务里填写" in exc.value.detail


def test_count_matches_per_profile():
    flights = [
        ("A", "2026-04-14T05:35:44+00:00"),
        ("B", "2026-04-19T05:10:43+00:00"),
        ("C", "2026-04-29T05:34:09+00:00"),
        ("D", "2026-04-10T06:24:36+00:00"),
    ]
    counts = mounts.count_matches(PROFILES, {"C": P0414.id}, flights)
    assert counts == {str(P0414.id): 2, str(P0419.id): 1}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py`
Expected: FAIL，`ImportError: cannot import name 'pushbroom_mounts'`

- [ ] **Step 3: 建表**

新建 `db/migrations/031_pushbroom_mounts.sql`：

```sql
-- 推扫安装配置：架次按飞行日期选生效日期不晚于它的最近一组，手动指定优先。
CREATE TABLE IF NOT EXISTS pushbroom_mount_profiles (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  effective_from DATE NOT NULL UNIQUE,
  along_offset_m DOUBLE PRECISION NOT NULL,
  along_se_m DOUBLE PRECISION NOT NULL,
  boresight_px DOUBLE PRECISION NOT NULL,
  boresight_se_px DOUBLE PRECISION NOT NULL,
  flights JSONB NOT NULL DEFAULT '[]',
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 单个架次手动指定的配置。配置删除时一并删除。
CREATE TABLE IF NOT EXISTS pushbroom_flight_profiles (
  flight_name TEXT PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES pushbroom_mount_profiles(id) ON DELETE CASCADE,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 后台标定记录。页面轮询状态，离开页面结果也在。
CREATE TABLE IF NOT EXISTS pushbroom_calibration_runs (
  id UUID PRIMARY KEY,
  status VARCHAR(16) NOT NULL,
  input_dirs JSONB NOT NULL,
  result JSONB,
  error TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pushbroom_calibration_runs_created ON pushbroom_calibration_runs (created_at DESC);
```

- [ ] **Step 4: 加模型**

`models.py` 第 3–6 行改为：

```python
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
```

在 `class PushbroomCalibration` 之后、`class IncrementalRound` 之前插入：

```python
class PushbroomMountProfile(Base):
    """推扫安装配置。架次按飞行日期选生效日期不晚于它的最近一组。"""

    __tablename__ = "pushbroom_mount_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True)
    effective_from: Mapped[date] = mapped_column(Date, unique=True)
    along_offset_m: Mapped[float] = mapped_column(Float)
    along_se_m: Mapped[float] = mapped_column(Float)
    boresight_px: Mapped[float] = mapped_column(Float)
    boresight_se_px: Mapped[float] = mapped_column(Float)
    flights: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PushbroomFlightProfile(Base):
    """单个架次手动指定的安装配置，键是架次目录名。"""

    __tablename__ = "pushbroom_flight_profiles"
    flight_name: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pushbroom_mount_profiles.id", ondelete="CASCADE")
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PushbroomCalibrationRun(Base):
    """一次后台标定。status：running / done / failed。"""

    __tablename__ = "pushbroom_calibration_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16))
    input_dirs: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`PushbroomCalibration` 的文档字符串改为：`"""旧版单组相机标定。只在启动时迁移成安装配置，不再写入。"""`

- [ ] **Step 5: 写选配置规则**

新建 `app/services/pushbroom_mounts.py`：

```python
"""推扫安装配置：按飞行日期选配置、手动指定、抄进任务。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PushbroomFlightProfile, PushbroomMountProfile

CST = timezone(timedelta(hours=8))
BUILTIN = {"along_offset_m": 1.7, "boresight_px": -19.0}
_JOB_FORBIDDEN = ("along_offset_m", "boresight_px", "mount_profile")


@dataclass(frozen=True)
class Resolved:
    # manual：手动指定；date：按生效日期；builtin：没有对应配置
    source: str
    profile: Any | None = None


def flight_date(started_at: str | None) -> date | None:
    """飞行开始时间对应的北京时间日期。"""
    if not started_at:
        return None
    try:
        moment = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(CST).date()


def resolve(profiles: Iterable[Any], override_id: uuid.UUID | None, day: date | None) -> Resolved:
    rows = list(profiles)
    if override_id is not None:
        for row in rows:
            if row.id == override_id:
                return Resolved("manual", row)
    if day is not None:
        eligible = [row for row in rows if row.effective_from <= day]
        if eligible:
            return Resolved("date", max(eligible, key=lambda row: row.effective_from))
    return Resolved("builtin")


def mount_out(res: Resolved) -> dict[str, Any]:
    if res.profile is None:
        return {"id": None, "name": None, "source": "builtin", **BUILTIN}
    row = res.profile
    return {
        "id": str(row.id),
        "name": row.name,
        "source": res.source,
        "along_offset_m": row.along_offset_m,
        "boresight_px": row.boresight_px,
    }


def apply_mount(params: dict[str, Any], res: Resolved) -> dict[str, Any]:
    """把选中的配置抄进任务参数。内置时不写数值，由 worker 用自己的内置值。"""
    if any(key in params for key in _JOB_FORBIDDEN):
        raise HTTPException(400, "安装参数由安装配置决定，不能在任务里填写")
    out = dict(params)
    if res.profile is None:
        out["mount_profile"] = {"source": "builtin"}
        return out
    row = res.profile
    out["along_offset_m"] = float(row.along_offset_m)
    out["boresight_px"] = float(row.boresight_px)
    out["mount_profile"] = {"id": str(row.id), "name": row.name, "source": res.source}
    return out


def count_matches(
    profiles: list[Any], overrides: dict[str, uuid.UUID], flights: list[tuple[str, str | None]]
) -> dict[str, int]:
    counts = {str(row.id): 0 for row in profiles}
    for name, started_at in flights:
        res = resolve(profiles, overrides.get(name), flight_date(started_at))
        if res.profile is not None:
            counts[str(res.profile.id)] += 1
    return counts


def load_profiles(db: Session) -> list[PushbroomMountProfile]:
    return list(db.scalars(select(PushbroomMountProfile).order_by(PushbroomMountProfile.effective_from.asc())))


def load_overrides(db: Session) -> dict[str, uuid.UUID]:
    return {row.flight_name: row.profile_id for row in db.scalars(select(PushbroomFlightProfile))}


def resolve_for(db: Session, flight_name: str, started_at: str | None) -> Resolved:
    return resolve(load_profiles(db), load_overrides(db).get(flight_name), flight_date(started_at))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add mosaic/server/db/migrations/031_pushbroom_mounts.sql mosaic/server/backend/app/models.py \
  mosaic/server/backend/app/services/pushbroom_mounts.py mosaic/server/backend/tests/test_pushbroom_mounts.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：安装配置的表和按飞行日期选配置的规则。')"
```

---

### Task 3: 标定改为后台运行

**Files:**
- Rewrite: `mosaic/server/backend/app/services/pushbroom_calibration.py`
- Rewrite: `mosaic/server/backend/tests/test_pushbroom_calibration.py`

**Interfaces:**
- Consumes: `PushbroomCalibrationRun`（Task 2）
- Produces（`app.services.pushbroom_calibration`）：
  - `TOKEN_SECONDS = 1800`，`STALE_ERROR = "服务重启，标定中断"`
  - `issue_run_token(settings, run_id: str, expires: datetime) -> str`
  - `assert_run_token(settings, token: str, run_id: str) -> None`（失败 400「标定结果已失效，请重新标定」）
  - `usable_combined(result: dict | None) -> tuple[dict[str, float], list[str]]`（四个数值已取位；不可用 400）
  - `run_out(settings, row, username: str | None = None) -> dict`
  - `start_run(db, settings, user, input_dirs: list[str], *, launch=None) -> PushbroomCalibrationRun`
  - `execute_run(settings, run_id, input_dirs, *, rpc=None, session_factory=None) -> None`
  - `list_runs(db) -> list[PushbroomCalibrationRun]`（最近 10 次）
  - `get_run(db, run_id) -> PushbroomCalibrationRun`（不存在 404）
  - `fail_stale_runs(db) -> int`
- 删除：`latest_calibration`、`with_active_calibration`、`public_state`、`present_run`、`apply_calibration`、`run_calibrate`、`BUILTIN`（改用 `pushbroom_mounts.BUILTIN`）。`pushbroom.py` 和路由里的引用在 Task 5、Task 6 里改；本任务结束时只跑本任务的测试文件。

- [ ] **Step 1: 写失败的测试**

整个替换 `tests/test_pushbroom_calibration.py`：

```python
"""后台标定：同时只一次、结果写回、重启收口、凭证只认这次标定且 30 分钟有效。"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import pushbroom_calibration as cal

SETTINGS = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
COMBINED = {"usable": True, "along_offset_m": 1.784, "boresight_px": -49.83, "along_se_m": 0.114, "boresight_se_px": 2.94}
USABLE = {
    "flights": [
        {"flight": "FigSpec_A", "calibration": {"usable": True}},
        {"flight": "FigSpec_B", "calibration": {"usable": False}},
    ],
    "combined": COMBINED,
}
USER = SimpleNamespace(id=7, username="admin")


def _row(**kw):
    base = dict(
        id=uuid.uuid4(),
        status="done",
        input_dirs=["/data/input/pushbroom/FigSpec_A", "/data/input/pushbroom/FigSpec_B"],
        result=USABLE,
        error=None,
        created_by=7,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        finished_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _RunDb:
    def __init__(self, row):
        self.row = row
        self.committed = False
        self.closed = False

    def get(self, _model, _id):
        return self.row

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_start_refuses_while_another_run_is_going():
    db = MagicMock()
    db.scalar.return_value = _row(status="running")
    with pytest.raises(HTTPException) as exc:
        cal.start_run(db, SETTINGS, USER, ["/d/A"], launch=lambda *_: None)
    assert exc.value.status_code == 409


def test_start_records_running_and_launches_once():
    db = MagicMock()
    db.scalar.return_value = None
    calls = []
    row = cal.start_run(db, SETTINGS, USER, ["/d/A", "/d/B"], launch=lambda *args: calls.append(args))
    assert row.status == "running" and row.input_dirs == ["/d/A", "/d/B"] and row.created_by == 7
    assert calls == [(SETTINGS, row.id, ["/d/A", "/d/B"])]
    db.add.assert_called()
    db.commit.assert_called()


def test_execute_writes_result_back():
    row = _row(status="running", result=None, finished_at=None)
    db = _RunDb(row)
    cal.execute_run(SETTINGS, row.id, row.input_dirs, rpc=lambda dirs: USABLE, session_factory=lambda: db)
    assert row.status == "done" and row.result == USABLE and row.finished_at is not None
    assert db.committed and db.closed


def test_execute_records_failure_reason():
    row = _row(status="running", result=None, finished_at=None)

    def boom(_dirs):
        raise RuntimeError("标定超时")

    cal.execute_run(SETTINGS, row.id, row.input_dirs, rpc=boom, session_factory=lambda: _RunDb(row))
    assert row.status == "failed" and row.error == "标定超时"
    row2 = _row(status="running", result=None, finished_at=None)
    cal.execute_run(SETTINGS, row2.id, [], rpc=lambda d: {"error": "进程退出"}, session_factory=lambda: _RunDb(row2))
    assert row2.status == "failed" and row2.error == "进程退出"


def test_execute_leaves_rows_that_are_no_longer_running():
    row = _row(status="failed", error=cal.STALE_ERROR)
    cal.execute_run(SETTINGS, row.id, [], rpc=lambda d: USABLE, session_factory=lambda: _RunDb(row))
    assert row.status == "failed" and row.error == cal.STALE_ERROR


def test_fail_stale_runs_marks_running_as_interrupted():
    stale = _row(status="running", finished_at=None)
    db = MagicMock()
    db.scalars.return_value = [stale]
    assert cal.fail_stale_runs(db) == 1
    assert stale.status == "failed" and stale.error == cal.STALE_ERROR and stale.finished_at is not None


def test_run_out_issues_token_only_for_fresh_usable_result():
    row = _row()
    out = cal.run_out(SETTINGS, row, "admin")
    assert out["flights"] == ["FigSpec_A", "FigSpec_B"]
    assert out["created_by"] == "admin"
    cal.assert_run_token(SETTINGS, out["run_token"], str(row.id))
    with pytest.raises(HTTPException):
        cal.assert_run_token(SETTINGS, out["run_token"], str(uuid.uuid4()))
    old = _row(finished_at=datetime.now(timezone.utc) - timedelta(minutes=31))
    assert "run_token" not in cal.run_out(SETTINGS, old)
    unusable = _row(result={"flights": [], "combined": {"usable": False, "note": "往返航带不够"}})
    assert "run_token" not in cal.run_out(SETTINGS, unusable)
    assert "run_token" not in cal.run_out(SETTINGS, _row(status="running", result=None, finished_at=None))


def test_expired_token_is_rejected():
    row_id = str(uuid.uuid4())
    token = cal.issue_run_token(SETTINGS, row_id, datetime.now(timezone.utc) - timedelta(seconds=5))
    with pytest.raises(HTTPException) as exc:
        cal.assert_run_token(SETTINGS, token, row_id)
    assert "重新标定" in exc.value.detail


def test_usable_combined_rounds_and_lists_usable_flights():
    values, names = cal.usable_combined(USABLE)
    assert values == {"along_offset_m": 1.78, "boresight_px": -49.8, "along_se_m": 0.11, "boresight_se_px": 2.9}
    assert names == ["FigSpec_A"]
    with pytest.raises(HTTPException):
        cal.usable_combined({"flights": [], "combined": {"usable": False}})
    with pytest.raises(HTTPException):
        cal.usable_combined({**USABLE, "combined": {**COMBINED, "boresight_px": 900}})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_calibration.py`
Expected: FAIL，`AttributeError: module ... has no attribute 'start_run'`

- [ ] **Step 3: 重写服务**

整个替换 `app/services/pushbroom_calibration.py`：

```python
"""推扫相机安装标定。在后台线程里调计算服务，结果写进标定记录，页面轮询。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomCalibrationRun, User
from app.services.audit import record_audit
from app.services.mq import queue_stats

log = logging.getLogger(__name__)

TOKEN_TYPE = "pushbroom_calibration"
TOKEN_SECONDS = 30 * 60
QUEUE_CALIBRATE = "pushbroom.calibrate"
RUNS_KEPT = 10
STALE_ERROR = "服务重启，标定中断"
_DIGITS = {"along_offset_m": 2, "boresight_px": 1, "along_se_m": 2, "boresight_se_px": 1}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def issue_run_token(settings: Settings, run_id: str, expires: datetime) -> str:
    payload = {"typ": TOKEN_TYPE, "run": run_id, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def assert_run_token(settings: Settings, token: str, run_id: str) -> None:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "标定结果已失效，请重新标定") from exc
    if claims.get("typ") != TOKEN_TYPE or claims.get("run") != run_id:
        raise HTTPException(400, "标定结果已失效，请重新标定")


def usable_names(result: dict[str, Any]) -> list[str]:
    names = []
    for item in result.get("flights") or []:
        if not isinstance(item, dict):
            continue
        cal = item.get("calibration") or {}
        if cal.get("usable") and item.get("flight"):
            names.append(str(item["flight"]))
    return names


def _has_usable(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    return bool((result.get("combined") or {}).get("usable")) and bool(usable_names(result))


def usable_combined(result: dict[str, Any] | None) -> tuple[dict[str, float], list[str]]:
    """可保存的合并值（已取位）和参与的架次。不可用或超出范围时 400。"""
    if not _has_usable(result):
        raise HTTPException(400, "这次标定没有可用结果")
    combined = result["combined"]  # type: ignore[index]
    try:
        values = {key: round(float(combined[key]), digits) for key, digits in _DIGITS.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "这次标定没有可用结果") from exc
    if not -20 <= values["along_offset_m"] <= 20:
        raise HTTPException(400, "沿航向偏移超出范围")
    if not -400 <= values["boresight_px"] <= 400:
        raise HTTPException(400, "狭缝中心偏置超出范围")
    if values["along_se_m"] < 0 or values["boresight_se_px"] < 0:
        raise HTTPException(400, "不确定度无效")
    return values, usable_names(result)  # type: ignore[arg-type]


def run_out(settings: Settings, row: Any, username: str | None = None) -> dict[str, Any]:
    dirs = list(row.input_dirs or [])
    out: dict[str, Any] = {
        "id": str(row.id),
        "status": row.status,
        "input_dirs": dirs,
        "flights": [Path(d).name for d in dirs],
        "result": row.result,
        "error": row.error,
        "created_by": username,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
        "expires_at": None,
    }
    if row.status == "done" and row.finished_at is not None and _has_usable(row.result):
        expires = row.finished_at + timedelta(seconds=TOKEN_SECONDS)
        if expires > _utcnow():
            out["run_token"] = issue_run_token(settings, str(row.id), expires)
            out["expires_at"] = expires.isoformat()
    return out


def _online(settings: Settings) -> bool:
    consumers = queue_stats(settings.rabbitmq_url, QUEUE_CALIBRATE).get("consumers")
    return bool(consumers)


def _rpc(settings: Settings, input_dirs: list[str]) -> dict[str, Any]:
    import pika

    timeout = min(600, len(input_dirs) * 120) + 15
    conn = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    box: dict[str, bytes] = {}
    try:
        ch = conn.channel()
        ch.queue_declare(queue=QUEUE_CALIBRATE, durable=True)
        reply = ch.queue_declare(queue="", exclusive=True)
        corr = str(uuid.uuid4())
        ch.basic_publish(
            exchange="",
            routing_key=QUEUE_CALIBRATE,
            body=json.dumps({"schema_version": 1, "input_dirs": input_dirs}, ensure_ascii=False).encode(),
            properties=pika.BasicProperties(reply_to=reply.method.queue, correlation_id=corr, content_type="application/json"),
        )

        def on_message(_ch, _method, props, payload: bytes) -> None:
            if props.correlation_id == corr:
                box["body"] = payload
                _ch.stop_consuming()

        ch.basic_consume(queue=reply.method.queue, on_message_callback=on_message, auto_ack=True)
        conn.call_later(timeout, lambda: ch.stop_consuming())
        ch.start_consuming()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if "body" not in box:
        raise RuntimeError("标定超时")
    try:
        data = json.loads(box["body"].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("标定没有返回结果") from exc
    if not isinstance(data, dict):
        raise RuntimeError("标定没有返回结果")
    return data


def _launch(settings: Settings, run_id: uuid.UUID, input_dirs: list[str]) -> None:
    threading.Thread(
        target=execute_run, args=(settings, run_id, input_dirs), name="pushbroom-calibrate", daemon=True
    ).start()


def start_run(
    db: Session,
    settings: Settings,
    user: User,
    input_dirs: list[str],
    *,
    launch: Callable[[Settings, uuid.UUID, list[str]], None] | None = None,
) -> PushbroomCalibrationRun:
    """建一条进行中的标定记录，交给后台线程，立刻返回。"""
    busy = db.scalar(select(PushbroomCalibrationRun).where(PushbroomCalibrationRun.status == "running").limit(1))
    if busy is not None:
        raise HTTPException(409, "已有一次标定在进行，请等它结束")
    if launch is None and not _online(settings):
        raise HTTPException(409, "推扫计算服务未在线")
    run_id = uuid.uuid4()
    row = PushbroomCalibrationRun(
        id=run_id,
        status="running",
        input_dirs=list(input_dirs),
        created_by=user.id,
        created_at=_utcnow(),
    )
    db.add(row)
    record_audit(db, user.id, "pushbroom.calibration.start", {"run_id": str(run_id), "input_dirs": list(input_dirs)})
    (launch or _launch)(settings, run_id, list(input_dirs))
    return row


def execute_run(
    settings: Settings,
    run_id: uuid.UUID,
    input_dirs: list[str],
    *,
    rpc: Callable[[list[str]], dict[str, Any]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """后台线程：调计算服务，把结果或失败原因写回记录。"""
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    call = rpc or (lambda dirs: _rpc(settings, dirs))
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        data = call(input_dirs)
        if not isinstance(data, dict) or "combined" not in data:
            detail = data.get("error") if isinstance(data, dict) else None
            raise RuntimeError(detail or "标定没有返回结果")
        result = data
    except Exception as exc:  # noqa: BLE001
        log.warning("pushbroom calibrate %s failed: %s", run_id, exc)
        error = str(exc) or "标定失败"
    db = session_factory()
    try:
        row = db.get(PushbroomCalibrationRun, run_id)
        if row is None or row.status != "running":
            return
        row.status = "failed" if error else "done"
        row.result = result
        row.error = error
        row.finished_at = _utcnow()
        db.commit()
    finally:
        db.close()


def list_runs(db: Session) -> list[PushbroomCalibrationRun]:
    q = select(PushbroomCalibrationRun).order_by(PushbroomCalibrationRun.created_at.desc()).limit(RUNS_KEPT)
    return list(db.scalars(q))


def get_run(db: Session, run_id: uuid.UUID) -> PushbroomCalibrationRun:
    row = db.get(PushbroomCalibrationRun, run_id)
    if row is None:
        raise HTTPException(404, "标定记录不存在")
    return row


def fail_stale_runs(db: Session) -> int:
    """启动时把上次没跑完的标定记成中断。后台线程随进程一起没了。"""
    rows = list(db.scalars(select(PushbroomCalibrationRun).where(PushbroomCalibrationRun.status == "running")))
    now = _utcnow()
    for row in rows:
        row.status = "failed"
        row.error = STALE_ERROR
        row.finished_at = now
    if rows:
        db.commit()
    return len(rows)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_calibration.py`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

本任务结束时 `pushbroom.py` 和路由仍引用已删除的函数，Task 5、Task 6 会改掉。中间这两次提交之间不要重启后端容器（`--reload` 会自动加载，开发环境短暂报错可以接受；如果介意，把 Task 3、5、6 连着做完再提交）。

```bash
git add mosaic/server/backend/app/services/pushbroom_calibration.py mosaic/server/backend/tests/test_pushbroom_calibration.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：相机标定改为后台运行，结果写进标定记录。')"
```

---

### Task 4: 配置的增删改、手动指定和旧标定迁移

**Files:**
- Modify: `mosaic/server/backend/app/services/pushbroom_mounts.py`（文件末尾追加，import 补齐）
- Modify: `mosaic/server/backend/app/bootstrap.py:395-397`
- Test: `mosaic/server/backend/tests/test_pushbroom_mounts.py`（追加）

**Interfaces:**
- Consumes: `pushbroom_calibration.assert_run_token`、`usable_combined`（Task 3）
- Produces（`app.services.pushbroom_mounts`）：
  - `profile_out(row, matched: int, username: str | None) -> dict`
  - `create_profile(db, settings, user, *, run_id: uuid.UUID, run_token: str, name: str, effective_from: date) -> PushbroomMountProfile`
  - `update_profile(db, user, profile_id: uuid.UUID, *, name: str | None = None, effective_from: date | None = None) -> PushbroomMountProfile`
  - `delete_profile(db, user, profile_id: uuid.UUID) -> None`
  - `set_override(db, user, flight_name: str, profile_id: uuid.UUID | None) -> None`
  - `legacy_effective_date(row, dates: dict[str, date]) -> date`
  - `migrate_legacy(db, input_root: str) -> PushbroomMountProfile | None`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_pushbroom_mounts.py` 顶部 import 区补：

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.config import Settings
from app.services import pushbroom_calibration as cal
```

文件末尾追加：

```python
SETTINGS = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
USER = SimpleNamespace(id=7, username="admin")
RESULT = {
    "flights": [{"flight": "FigSpec_260419_132008", "calibration": {"usable": True}}],
    "combined": {"usable": True, "along_offset_m": 1.314, "boresight_px": -30.26, "along_se_m": 0.2, "boresight_se_px": 3.1},
}


def _done_run():
    return SimpleNamespace(id=uuid.uuid4(), status="done", result=RESULT, finished_at=datetime.now(timezone.utc))


def _token(run):
    return cal.issue_run_token(SETTINGS, str(run.id), datetime.now(timezone.utc) + timedelta(minutes=5))


def test_create_profile_takes_values_from_the_run(monkeypatch):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [P0414])
    db = MagicMock()
    db.get.return_value = run
    row = mounts.create_profile(
        db, SETTINGS, USER, run_id=run.id, run_token=_token(run), name="  0419 安装 ", effective_from=date(2026, 4, 19)
    )
    assert row.name == "0419 安装" and row.effective_from == date(2026, 4, 19)
    assert (row.along_offset_m, row.boresight_px, row.along_se_m, row.boresight_se_px) == (1.31, -30.3, 0.2, 3.1)
    assert row.flights == ["FigSpec_260419_132008"] and row.created_by == 7
    db.add.assert_called()


def test_create_profile_rejects_token_of_another_run(monkeypatch):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [])
    db = MagicMock()
    db.get.return_value = run
    with pytest.raises(HTTPException) as exc:
        mounts.create_profile(db, SETTINGS, USER, run_id=run.id, run_token=_token(_done_run()), name="x", effective_from=date(2026, 5, 1))
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("name", "day", "word"),
    [("0414 安装", date(2026, 5, 1), "名称"), ("新的", date(2026, 4, 14), "生效日期"), ("  ", date(2026, 5, 1), "名称")],
)
def test_create_profile_rejects_duplicate_or_blank(monkeypatch, name, day, word):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [P0414])
    db = MagicMock()
    db.get.return_value = run
    with pytest.raises(HTTPException) as exc:
        mounts.create_profile(db, SETTINGS, USER, run_id=run.id, run_token=_token(run), name=name, effective_from=day)
    assert word in exc.value.detail


def test_update_profile_changes_only_name_and_date(monkeypatch):
    row = _profile("旧名", date(2026, 4, 14))
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [row, P0419])
    db = MagicMock()
    db.get.return_value = row
    mounts.update_profile(db, USER, row.id, name="0414 安装", effective_from=date(2026, 4, 13))
    assert row.name == "0414 安装" and row.effective_from == date(2026, 4, 13)
    assert row.along_offset_m == 1.78
    with pytest.raises(HTTPException):
        mounts.update_profile(db, USER, row.id, effective_from=date(2026, 4, 19))


def test_delete_profile_also_drops_overrides():
    row = _profile("0414 安装", date(2026, 4, 14))
    db = MagicMock()
    db.get.return_value = row
    mounts.delete_profile(db, USER, row.id)
    db.execute.assert_called_once()
    db.delete.assert_called_once_with(row)


def test_set_override_creates_updates_and_clears():
    db = MagicMock()
    db.get.side_effect = lambda model, key: P0414 if model is mounts.PushbroomMountProfile else None
    mounts.set_override(db, USER, "FigSpec_A", P0414.id)
    added = db.add.call_args.args[0]
    assert added.flight_name == "FigSpec_A" and added.profile_id == P0414.id
    existing = SimpleNamespace(flight_name="FigSpec_A", profile_id=P0419.id)
    db2 = MagicMock()
    db2.get.side_effect = lambda model, key: P0414 if model is mounts.PushbroomMountProfile else existing
    mounts.set_override(db2, USER, "FigSpec_A", P0414.id)
    assert existing.profile_id == P0414.id
    db3 = MagicMock()
    db3.get.return_value = existing
    mounts.set_override(db3, USER, "FigSpec_A", None)
    db3.delete.assert_called_once_with(existing)


def test_legacy_effective_date_uses_earliest_flight_or_save_date():
    legacy = SimpleNamespace(flights=["A", "B"], created_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc))
    assert mounts.legacy_effective_date(legacy, {"A": date(2026, 4, 19), "B": date(2026, 4, 14)}) == date(2026, 4, 14)
    assert mounts.legacy_effective_date(legacy, {}) == date(2026, 11, 20)


def test_migrate_legacy_runs_once(monkeypatch):
    legacy = SimpleNamespace(
        along_offset_m=1.78,
        along_se_m=0.11,
        boresight_px=-49.8,
        boresight_se_px=2.9,
        flights=["FigSpec_260414_134505"],
        created_by=1,
        created_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(mounts, "_flight_dates", lambda root, names: {"FigSpec_260414_134505": date(2026, 4, 14)})
    db = MagicMock()
    db.scalar.side_effect = [None, legacy]
    row = mounts.migrate_legacy(db, "/data/input/pushbroom")
    assert row.name == "0414 安装" and row.effective_from == date(2026, 4, 14)
    assert row.along_offset_m == 1.78 and row.flights == ["FigSpec_260414_134505"]
    db.commit.assert_called_once()
    again = MagicMock()
    again.scalar.return_value = uuid.uuid4()
    assert mounts.migrate_legacy(again, "/data/input/pushbroom") is None
    again.add.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py`
Expected: 新加的用例 FAIL，`AttributeError: ... no attribute 'create_profile'`

- [ ] **Step 3: 实现**

`pushbroom_mounts.py` 的 import 区改为：

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomCalibration, PushbroomCalibrationRun, PushbroomFlightProfile, PushbroomMountProfile, User
from app.services.audit import record_audit
from app.services.pushbroom_calibration import assert_run_token, usable_combined
```

文件末尾追加：

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def profile_out(row: Any, matched: int, username: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "effective_from": row.effective_from.isoformat(),
        "along_offset_m": row.along_offset_m,
        "along_se_m": row.along_se_m,
        "boresight_px": row.boresight_px,
        "boresight_se_px": row.boresight_se_px,
        "flights": list(row.flights or []),
        "created_by": username,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "matched": matched,
    }


def _clean_name(name: str | None) -> str:
    text = (name or "").strip()
    if not text:
        raise HTTPException(400, "请填写名称")
    if len(text) > 64:
        raise HTTPException(400, "名称最多 64 个字")
    return text


def _assert_unique(db: Session, *, name: str | None, day: date | None, skip: uuid.UUID | None = None) -> None:
    for row in load_profiles(db):
        if row.id == skip:
            continue
        if name is not None and row.name == name:
            raise HTTPException(400, f"名称与「{row.name}」重复")
        if day is not None and row.effective_from == day:
            raise HTTPException(400, f"生效日期与「{row.name}」重复")


def _get_profile(db: Session, profile_id: uuid.UUID) -> PushbroomMountProfile:
    row = db.get(PushbroomMountProfile, profile_id)
    if row is None:
        raise HTTPException(404, "安装配置不存在")
    return row


def create_profile(
    db: Session,
    settings: Settings,
    user: User,
    *,
    run_id: uuid.UUID,
    run_token: str,
    name: str,
    effective_from: date,
) -> PushbroomMountProfile:
    """用一次标定的结果建配置。数值只从标定记录取。"""
    run = db.get(PushbroomCalibrationRun, run_id)
    if run is None or run.status != "done":
        raise HTTPException(400, "标定结果已失效，请重新标定")
    assert_run_token(settings, run_token, str(run.id))
    values, flights = usable_combined(run.result)
    clean = _clean_name(name)
    _assert_unique(db, name=clean, day=effective_from)
    now = _utcnow()
    row = PushbroomMountProfile(
        id=uuid.uuid4(),
        name=clean,
        effective_from=effective_from,
        flights=flights,
        created_by=user.id,
        created_at=now,
        updated_at=now,
        **values,
    )
    db.add(row)
    record_audit(
        db,
        user.id,
        "pushbroom.mount.create",
        {"name": clean, "effective_from": effective_from.isoformat(), "run_id": str(run.id), **values},
    )
    return row


def update_profile(
    db: Session,
    user: User,
    profile_id: uuid.UUID,
    *,
    name: str | None = None,
    effective_from: date | None = None,
) -> PushbroomMountProfile:
    """只改名称和生效日期，数值不能改。"""
    row = _get_profile(db, profile_id)
    clean = _clean_name(name) if name is not None else None
    _assert_unique(db, name=clean, day=effective_from, skip=row.id)
    before = {"name": row.name, "effective_from": row.effective_from.isoformat()}
    if clean is not None:
        row.name = clean
    if effective_from is not None:
        row.effective_from = effective_from
    row.updated_at = _utcnow()
    after = {"name": row.name, "effective_from": row.effective_from.isoformat()}
    record_audit(db, user.id, "pushbroom.mount.update", {"id": str(row.id), "before": before, "after": after})
    return row


def delete_profile(db: Session, user: User, profile_id: uuid.UUID) -> None:
    """删除配置和指向它的手动指定。已提交的任务各自存着数值，不受影响。"""
    row = _get_profile(db, profile_id)
    detail = {"id": str(row.id), "name": row.name, "effective_from": row.effective_from.isoformat()}
    db.execute(delete(PushbroomFlightProfile).where(PushbroomFlightProfile.profile_id == row.id))
    db.delete(row)
    record_audit(db, user.id, "pushbroom.mount.delete", detail)


def set_override(db: Session, user: User, flight_name: str, profile_id: uuid.UUID | None) -> None:
    """给单个架次手动指定配置；profile_id 为空表示撤销，回到按日期。"""
    name = (flight_name or "").strip()
    if not name or "/" in name or name in {".", ".."}:
        raise HTTPException(400, "架次名无效")
    existing = db.get(PushbroomFlightProfile, name)
    if profile_id is None:
        if existing is not None:
            db.delete(existing)
        record_audit(db, user.id, "pushbroom.mount.override", {"flight": name, "profile_id": None})
        return
    _get_profile(db, profile_id)
    if existing is None:
        db.add(PushbroomFlightProfile(flight_name=name, profile_id=profile_id, created_by=user.id, created_at=_utcnow()))
    else:
        existing.profile_id = profile_id
    record_audit(db, user.id, "pushbroom.mount.override", {"flight": name, "profile_id": str(profile_id)})


def legacy_effective_date(row: Any, dates: dict[str, date]) -> date:
    found = [dates[name] for name in row.flights or [] if name in dates]
    if found:
        return min(found)
    return (row.created_at or _utcnow()).astimezone(CST).date()


def _flight_dates(input_root: str, names: list[str]) -> dict[str, date]:
    from app.services.pushbroom_check import find_flights, quick_check

    wanted = set(names)
    out: dict[str, date] = {}
    for path in find_flights(input_root):
        if path.name not in wanted:
            continue
        day = flight_date(quick_check(path).flight.get("started_at"))
        if day is not None:
            out[path.name] = day
    return out


def migrate_legacy(db: Session, input_root: str) -> PushbroomMountProfile | None:
    """配置表为空、旧标定表有记录时，把最新一条转成一组配置。只做一次。"""
    if db.scalar(select(PushbroomMountProfile.id).limit(1)) is not None:
        return None
    legacy = db.scalar(select(PushbroomCalibration).order_by(PushbroomCalibration.created_at.desc()).limit(1))
    if legacy is None:
        return None
    day = legacy_effective_date(legacy, _flight_dates(input_root, list(legacy.flights or [])))
    row = PushbroomMountProfile(
        id=uuid.uuid4(),
        name=f"{day:%m%d} 安装",
        effective_from=day,
        along_offset_m=legacy.along_offset_m,
        along_se_m=legacy.along_se_m,
        boresight_px=legacy.boresight_px,
        boresight_se_px=legacy.boresight_se_px,
        flights=list(legacy.flights or []),
        created_by=legacy.created_by,
        created_at=legacy.created_at or _utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    return row
```

- [ ] **Step 4: 接入启动流程**

`bootstrap.py` 在 `log = logging.getLogger(__name__)` 前面补 import：

```python
from app.services import pushbroom_calibration, pushbroom_mounts
```

在文件里 `def bootstrap` 前面加：

```python
def _ensure_pushbroom_mounts(db: Session, settings: Settings) -> None:
    """推扫安装配置：建表、迁移旧标定、收口上次没跑完的标定。出错只记日志，不挡启动。"""
    _ensure_sql_named(db, "031_pushbroom_mounts.sql")
    try:
        pushbroom_mounts.migrate_legacy(db, settings.pushbroom_input_root)
        pushbroom_calibration.fail_stale_runs(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("pushbroom mounts bootstrap failed")
```

`bootstrap()` 里 `_ensure_sql_named(db, "030_camera_source.sql")` 下一行加：

```python
    _ensure_pushbroom_mounts(db, settings)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py tests/test_pushbroom_calibration.py`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add mosaic/server/backend/app/services/pushbroom_mounts.py mosaic/server/backend/app/bootstrap.py mosaic/server/backend/tests/test_pushbroom_mounts.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：安装配置的增删改、手动指定和旧标定迁移。')"
```

---

### Task 5: 任务侧：建任务用安装配置、架次统计、排队位置、日志北京时间

**Files:**
- Modify: `mosaic/server/backend/app/services/pushbroom.py`
- Test: `mosaic/server/backend/tests/test_pushbroom_jobs.py`

**Interfaces:**
- Consumes: `pushbroom_mounts.resolve_for`、`resolve`、`flight_date`、`mount_out`、`apply_mount`、`load_profiles`、`load_overrides`、`CST`
- Produces（`app.services.pushbroom`）：
  - `page_jobs(db, page, page_size, *, owner_id, status=None, input_dir: str | None = None)`
  - `active_jobs(db) -> list[PushbroomJob]`
  - `queue_positions(rows) -> dict[uuid.UUID, int]`
  - `flight_job_stats(db, owner_id: int | None) -> dict[str, dict]`：键是解析后的架次目录，值 `{"count": int, "latest": job | None, "latest_done": job | None}`
  - `flight_item(check: dict, mount: dict, stats: dict | None) -> dict`：在核对结果上加 `mount`、`job_count`、`latest_job`、`preview_job_id`
  - `flight_items(db, checks: list[FlightCheck], *, owner_id: int | None) -> list[dict]`：能用的按 `started_at` 倒序在前，不能用的按名称在后

- [ ] **Step 1: 写失败的测试**

`tests/test_pushbroom_jobs.py` 顶部 import 区补：

```python
from datetime import datetime, timedelta, timezone

from app.services import pushbroom_mounts as mounts
from app.services.pushbroom_check import FlightCheck
```

文件末尾追加：

```python
def test_logs_are_in_beijing_time(tmp_path):
    job = _job(tmp_path)
    row = SimpleNamespace(created_at=datetime(2026, 11, 21, 2, 3, 4, tzinfo=timezone.utc), level="info", message="已提交")
    db = MagicMock()
    db.scalars.return_value = [row]
    assert svc.format_logs(db, job) == "2026-11-21 10:03:04 [info] 已提交"


def test_queue_positions_count_running_then_queued_by_enqueue_time():
    t = datetime(2026, 11, 21, tzinfo=timezone.utc)
    run = SimpleNamespace(id=uuid.uuid4(), seq=1, status="running", updated_at=t, created_at=t)
    q1 = SimpleNamespace(id=uuid.uuid4(), seq=5, status="queued", updated_at=t + timedelta(minutes=1), created_at=t)
    q2 = SimpleNamespace(id=uuid.uuid4(), seq=3, status="queued", updated_at=t + timedelta(minutes=2), created_at=t)
    assert svc.queue_positions([q2, run, q1]) == {q1.id: 1, q2.id: 2}


def test_flight_items_attach_mount_and_latest_job(tmp_path, monkeypatch):
    good = FlightCheck(input_dir="/data/input/pushbroom/A", name="A", flight={"started_at": "2026-04-19T05:10:43+00:00"})
    older = FlightCheck(input_dir="/data/input/pushbroom/B", name="B", flight={"started_at": "2026-04-14T05:35:44+00:00"})
    bad = FlightCheck(input_dir="/data/input/pushbroom/C", name="C", errors=["缺少文件：figspec.gps"])
    profile = SimpleNamespace(id=uuid.uuid4(), name="0414 安装", effective_from=datetime(2026, 4, 14).date(), along_offset_m=1.78, boresight_px=-49.8)
    latest = _job(tmp_path, status="running", global_percent=40.0, current_stage="P3_align")
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [profile])
    monkeypatch.setattr(mounts, "load_overrides", lambda db: {})
    monkeypatch.setattr(
        svc, "flight_job_stats", lambda db, owner_id: {"/data/input/pushbroom/A": {"count": 2, "latest": latest, "latest_done": None}}
    )
    items = svc.flight_items(MagicMock(), [bad, older, good], owner_id=None)
    assert [i["name"] for i in items] == ["A", "B", "C"]
    a = items[0]
    assert a["mount"]["name"] == "0414 安装" and a["mount"]["source"] == "date"
    assert a["job_count"] == 2 and a["latest_job"]["status"] == "running" and a["latest_job"]["global_percent"] == 40.0
    assert a["preview_job_id"] is None
    assert items[1]["job_count"] == 0 and items[1]["latest_job"] is None
    assert items[2]["ok"] is False


def test_create_job_copies_the_matched_mount(tmp_path, monkeypatch):
    inp = tmp_path / "in" / "FigSpec_X"
    inp.mkdir(parents=True)
    settings = Settings(
        data_roots=str(tmp_path), pushbroom_input_root=str(tmp_path / "in"), pushbroom_output_root=str(tmp_path / "out")
    )
    check = FlightCheck(input_dir=str(inp), name="FigSpec_X", flight={"bands": 10, "started_at": "2026-04-19T05:10:43+00:00"})
    profile = SimpleNamespace(id=uuid.uuid4(), name="0419 安装", effective_from=None, along_offset_m=1.3, boresight_px=-30.0)
    seen = {}

    def fake_resolve(db, name, started_at):
        seen["args"] = (name, started_at)
        return mounts.Resolved("date", profile)

    monkeypatch.setattr(svc, "quick_check", lambda path: check)
    monkeypatch.setattr(svc, "validate_paths", lambda *a, **k: None)
    monkeypatch.setattr(mounts, "resolve_for", fake_resolve)
    user = SimpleNamespace(id=1, username="admin")
    job = svc.create_job(MagicMock(), settings, FakeMQPublisher(), user=user, admin=True, input_dir=str(inp), title="", params={})
    assert seen["args"] == ("FigSpec_X", "2026-04-19T05:10:43+00:00")
    assert job.params["along_offset_m"] == 1.3 and job.params["boresight_px"] == -30.0
    assert job.params["mount_profile"] == {"id": str(profile.id), "name": "0419 安装", "source": "date"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_jobs.py`
Expected: 收集失败或新用例 FAIL（`pushbroom.py` 还在 import 已删除的 `latest_calibration`）

- [ ] **Step 3: 实现**

`pushbroom.py` 第 25 行：

```python
from app.services.pushbroom_calibration import latest_calibration, with_active_calibration
```

替换为：

```python
from app.services import pushbroom_mounts as mounts
```

并把 `from app.services.pushbroom_check import quick_check` 改为：

```python
from app.services.pushbroom_check import FlightCheck, quick_check
```

`create_job` 里：

```python
    params = with_active_calibration(params, latest_calibration(db))
```

替换为：

```python
    params = mounts.apply_mount(params, mounts.resolve_for(db, inp.name, check.flight.get("started_at")))
```

`page_jobs` 整个替换为：

```python
def page_jobs(
    db: Session,
    page: int,
    page_size: int,
    *,
    owner_id: int | None,
    status: str | None = None,
    input_dir: str | None = None,
) -> tuple[list[PushbroomJob], int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(PushbroomJob)
    cq = select(func.count()).select_from(PushbroomJob)
    if owner_id is not None:
        q = q.where(PushbroomJob.created_by == owner_id)
        cq = cq.where(PushbroomJob.created_by == owner_id)
    if status:
        q = q.where(PushbroomJob.status == status)
        cq = cq.where(PushbroomJob.status == status)
    if input_dir:
        q = q.where(PushbroomJob.input_dir == input_dir)
        cq = cq.where(PushbroomJob.input_dir == input_dir)
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(q.order_by(PushbroomJob.seq.desc()).offset((page - 1) * page_size).limit(page_size)))
    return rows, total


def active_jobs(db: Session) -> list[PushbroomJob]:
    return list(db.scalars(select(PushbroomJob).where(PushbroomJob.status.in_(ACTIVE))))


def queue_positions(rows: list[Any]) -> dict[uuid.UUID, int]:
    """排队任务前面还有几个：在跑的都算，排队的按入队时间。worker 一次只跑一个。"""
    running = sum(1 for row in rows if row.status in {"running", "cancelling"})
    queued = sorted(
        (row for row in rows if row.status == "queued"),
        key=lambda row: (row.updated_at or row.created_at, row.seq),
    )
    return {row.id: running + index for index, row in enumerate(queued)}


def flight_job_stats(db: Session, owner_id: int | None) -> dict[str, dict[str, Any]]:
    """每个架次目录跑过几次、最近一次、最近一次成功。两次查询取完，不按架次逐个查。"""
    q = select(
        PushbroomJob.input_dir,
        func.count().label("n"),
        func.max(PushbroomJob.seq).label("last"),
        func.max(PushbroomJob.seq).filter(PushbroomJob.status == "succeeded").label("done"),
    ).group_by(PushbroomJob.input_dir)
    if owner_id is not None:
        q = q.where(PushbroomJob.created_by == owner_id)
    stats = list(db.execute(q))
    seqs = {seq for row in stats for seq in (row.last, row.done) if seq is not None}
    jobs = {job.seq: job for job in db.scalars(select(PushbroomJob).where(PushbroomJob.seq.in_(seqs)))} if seqs else {}
    return {
        row.input_dir: {"count": int(row.n), "latest": jobs.get(row.last), "latest_done": jobs.get(row.done)}
        for row in stats
    }


def flight_item(check: dict[str, Any], mount: dict[str, Any], stats: dict[str, Any] | None) -> dict[str, Any]:
    latest = (stats or {}).get("latest")
    done = (stats or {}).get("latest_done")
    return {
        **check,
        "mount": mount,
        "job_count": int((stats or {}).get("count") or 0),
        "latest_job": None
        if latest is None
        else {
            "id": str(latest.id),
            "seq": latest.seq,
            "status": latest.status,
            "global_percent": latest.global_percent,
            "current_stage": latest.current_stage,
            "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
        },
        "preview_job_id": str(done.id) if done is not None and preview_png(done) is not None else None,
    }


def flight_items(db: Session, checks: list[FlightCheck], *, owner_id: int | None) -> list[dict[str, Any]]:
    profiles = mounts.load_profiles(db)
    overrides = mounts.load_overrides(db)
    stats = flight_job_stats(db, owner_id)
    items = []
    for check in checks:
        res = mounts.resolve(profiles, overrides.get(check.name), mounts.flight_date(check.flight.get("started_at")))
        key = str(Path(check.input_dir).resolve())
        items.append(flight_item(check.as_dict(), mounts.mount_out(res), stats.get(key)))
    ok = sorted((i for i in items if i["ok"]), key=lambda i: i["flight"].get("started_at") or "", reverse=True)
    bad = sorted((i for i in items if not i["ok"]), key=lambda i: i["name"])
    return ok + bad
```

`format_logs` 里的时间格式化改为北京时间：

```python
    lines = [
        f"{(r.created_at or _utcnow()).astimezone(mounts.CST):%Y-%m-%d %H:%M:%S} [{r.level}] {r.message}"
        for r in rows
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_jobs.py tests/test_pushbroom_mounts.py`
Expected: 除 `test_seed_grants_pushbroom_once_to_roles_that_can_execute` 以外全部 PASS（它在 Task 7 改）。如果它现在仍然通过也正常。

- [ ] **Step 5: 提交**

```bash
git add mosaic/server/backend/app/services/pushbroom.py mosaic/server/backend/tests/test_pushbroom_jobs.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：建任务按安装配置写参数，架次带出最近任务，排队位置，日志改北京时间。')"
```

---

### Task 6: 接口

**Files:**
- Modify: `mosaic/server/backend/app/routers/pushbroom.py`
- Test: `mosaic/server/backend/tests/test_pushbroom_mounts.py`（追加权限用例）

**Interfaces:**
- Consumes: Task 3、4、5 的服务函数
- Produces（HTTP）：

| 方法和路径 | 角色 | 返回 |
| --- | --- | --- |
| `GET /api/pushbroom/flights` | executor、admin | `{input_root, output_root, items: flight_item[]}` |
| `POST /api/pushbroom/check` | executor、admin | `flight_item` |
| `GET /api/pushbroom/jobs?input_dir=` | executor、admin | 任务列表，每条带 `queue_position` |
| `GET /api/pushbroom/mounts` | admin | `{items: profile_out[], builtin}` |
| `POST /api/pushbroom/mounts` | admin | `profile_out` |
| `PATCH /api/pushbroom/mounts/{id}` | admin | `profile_out` |
| `DELETE /api/pushbroom/mounts/{id}` | admin | `{ok: true}` |
| `PUT /api/pushbroom/flights/{name}/mount` | admin | `{ok: true}` |
| `POST /api/pushbroom/calibration/runs` | admin | `run_out` |
| `GET /api/pushbroom/calibration/runs` | admin | `{items: run_out[]}` |
| `GET /api/pushbroom/calibration/runs/{id}` | admin | `run_out` |

删除 `GET /calibration`、`POST /calibration/run`、`POST /calibration`。

- [ ] **Step 1: 写失败的测试**

`tests/test_pushbroom_mounts.py` 末尾追加：

```python
def test_mount_and_calibration_routes_are_admin_only():
    import inspect

    from app.routers import pushbroom as routes

    for fn in (
        routes.list_mounts,
        routes.create_mount,
        routes.update_mount,
        routes.delete_mount,
        routes.set_flight_mount,
        routes.start_calibration,
        routes.list_calibration_runs,
        routes.get_calibration_run,
    ):
        params = inspect.signature(fn).parameters
        name = "user" if "user" in params else "_user"
        allowed = params[name].default.dependency.__closure__[0].cell_contents
        assert allowed == ("admin",), fn.__name__
    assert not hasattr(routes, "apply_calibration")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py -k routes`
Expected: FAIL（`ImportError` 或 `AttributeError: list_mounts`）

- [ ] **Step 3: 改路由**

`routers/pushbroom.py`：

1. import 区：

```python
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
```

以及

```python
from app.services import pushbroom as svc
from app.services import pushbroom_calibration as calibration
from app.services import pushbroom_mounts as mounts
```

2. `PushbroomParams` 里两行注释改为 `# 只为拒绝任务里手填安装参数；真正的值由安装配置决定`。

3. 删掉 `class CalibrationApplyBody`，在 `class CalibrationRunBody` 后面加：

```python
class MountCreateBody(BaseModel):
    run_id: uuid.UUID
    run_token: str
    name: str = Field(min_length=1, max_length=64)
    effective_from: date


class MountUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    effective_from: date | None = None


class FlightMountBody(BaseModel):
    profile_id: uuid.UUID | None = None
```

4. `_job_out` 签名和末尾：

```python
def _job_out(job: PushbroomJob, owner: str | None = None, queue_position: int | None = None) -> dict[str, Any]:
```

在返回字典最后加一项 `"queue_position": queue_position,`。

5. `_owner_name` 替换为：

```python
def _username(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = db.get(User, user_id)
    return row.username if row else None


def _owner_name(db: Session, job: PushbroomJob) -> str | None:
    return _username(db, job.created_by)


def _out(db: Session, job: PushbroomJob) -> dict[str, Any]:
    position = svc.queue_positions(svc.active_jobs(db)).get(job.id) if job.status == "queued" else None
    return _job_out(job, _owner_name(db, job), position)
```

6. 删掉 `get_calibration`、`run_calibration`、`apply_calibration` 三个函数，换成：

```python
def _calibration_dirs(user: User, settings: Settings, raw_dirs: list[str]) -> list[str]:
    input_root, _ = svc.roots_for(user, settings, admin=True)
    dirs: list[str] = []
    for raw in raw_dirs:
        inp = svc.assert_input_allowed(raw, input_root, settings)
        check = quick_check(inp)
        if not check.ok:
            raise HTTPException(400, {"message": f"{inp.name} 核对未通过", "check": check.as_dict()})
        dirs.append(str(inp))
    return dirs


@router.post("/calibration/runs")
def start_calibration(
    body: CalibrationRunBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = calibration.start_run(db, settings, user, _calibration_dirs(user, settings, body.input_dirs))
    return calibration.run_out(settings, row, user.username)


@router.get("/calibration/runs")
def list_calibration_runs(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    _user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    rows = calibration.list_runs(db)
    return {"items": [calibration.run_out(settings, r, _username(db, r.created_by)) for r in rows]}


@router.get("/calibration/runs/{run_id}")
def get_calibration_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    _user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = calibration.get_run(db, run_id)
    return calibration.run_out(settings, row, _username(db, row.created_by))


@router.get("/mounts")
def list_mounts(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    input_root, _ = svc.roots_for(user, settings, admin=True)
    checks = [quick_check(p) for p in find_flights(input_root)]
    pairs = [(c.name, c.flight.get("started_at")) for c in checks if c.ok]
    profiles = mounts.load_profiles(db)
    counts = mounts.count_matches(profiles, mounts.load_overrides(db), pairs)
    items = [mounts.profile_out(p, counts.get(str(p.id), 0), _username(db, p.created_by)) for p in profiles]
    return {"items": items, "builtin": dict(mounts.BUILTIN)}


@router.post("/mounts")
def create_mount(
    body: MountCreateBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = mounts.create_profile(
        db,
        settings,
        user,
        run_id=body.run_id,
        run_token=body.run_token,
        name=body.name,
        effective_from=body.effective_from,
    )
    return mounts.profile_out(row, 0, user.username)


@router.patch("/mounts/{profile_id}")
def update_mount(
    profile_id: uuid.UUID,
    body: MountUpdateBody,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = mounts.update_profile(db, user, profile_id, name=body.name, effective_from=body.effective_from)
    return mounts.profile_out(row, 0, _username(db, row.created_by))


@router.delete("/mounts/{profile_id}")
def delete_mount(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    mounts.delete_profile(db, user, profile_id)
    return {"ok": True}


@router.put("/flights/{flight_name}/mount")
def set_flight_mount(
    flight_name: str,
    body: FlightMountBody,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    mounts.set_override(db, user, flight_name, body.profile_id)
    return {"ok": True}
```

7. `flights` 和 `check` 替换为：

```python
@router.get("/flights")
def flights(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    admin = is_admin(user)
    input_root, output_root = svc.roots_for(user, settings, admin=admin)
    checks = [quick_check(p) for p in find_flights(input_root)]
    items = svc.flight_items(db, checks, owner_id=None if admin else user.id)
    return {"input_root": input_root, "output_root": output_root, "items": items}


@router.post("/check")
def check(
    body: CheckBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    admin = is_admin(user)
    input_root, _ = svc.roots_for(user, settings, admin=admin)
    inp = svc.assert_input_allowed(body.input_dir, input_root, settings)
    return svc.flight_items(db, [quick_check(inp)], owner_id=None if admin else user.id)[0]
```

8. `create_job` 里的报错文案改为 `"安装参数由安装配置决定，不能在任务里填写"`。

9. `list_jobs` 替换为：

```python
@router.get("/jobs")
def list_jobs(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    input_dir: str | None = None,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    owner_id = None if is_admin(user) else user.id
    resolved = str(Path(input_dir).resolve()) if input_dir else None
    rows, total = svc.page_jobs(db, page, page_size, owner_id=owner_id, status=status, input_dir=resolved)
    positions = svc.queue_positions(svc.active_jobs(db))
    names: dict[int, str | None] = {}
    for row in rows:
        if row.created_by is not None and row.created_by not in names:
            names[row.created_by] = _owner_name(db, row)
    items = [
        _job_out(r, names.get(r.created_by) if r.created_by is not None else None, positions.get(r.id)) for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
```

10. `create_job`、`get_job`、`cancel_job`、`retry_job` 的返回改用 `_out(db, job)`；`get_job` 写成：

```python
    job = _owned(db, job_id, user)
    out = _out(db, job)
    out["calibration_warning"] = svc.calibration_warning(job.output_dir)
    return out
```

`create_job` 写成 `return _out(db, job)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_mounts.py tests/test_pushbroom_calibration.py tests/test_pushbroom_jobs.py tests/test_pushbroom_check.py`
Expected: 除 `test_seed_grants_pushbroom_once_to_roles_that_can_execute` 外全部 PASS

- [ ] **Step 5: 在开发环境冒烟**

```bash
cd mosaic/server
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml restart backend
sleep 8 && docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml logs --tail=40 backend | grep -iE "error|traceback|started" || true
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml exec -T postgres \
  psql -U mosaic -d mosaic_visual -c "select name, effective_from, along_offset_m, boresight_px from pushbroom_mount_profiles;"
```

Expected: 日志里没有 Traceback；表里有一行 `0414 安装 | 2026-04-14 | 1.78 | -49.8`。

再用管理员令牌调一次 `/api/pushbroom/flights`，确认每个架次带 `mount`、`job_count`、`latest_job`，0414 和 0419/0429 都对到「0414 安装」，0410 是「内置」：

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s localhost:8000/api/pushbroom/flights -H "Authorization: Bearer $TOKEN" | python3 -c '
import sys,json
for i in json.load(sys.stdin)["items"]:
    print(i["name"], i["ok"], i["flight"].get("started_at"), i["mount"]["source"], i["mount"]["name"], i["job_count"])'
```

登录接口的路径或字段名对不上时，先看 `app/routers/auth.py`，不要猜。

- [ ] **Step 6: 提交**

```bash
git add mosaic/server/backend/app/routers/pushbroom.py mosaic/server/backend/tests/test_pushbroom_mounts.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：安装配置和后台标定的接口，架次和任务接口带出新字段。')"
```

---

### Task 7: 菜单键迁移

**Files:**
- Modify: `mosaic/server/backend/app/services/rbac.py`
- Test: `mosaic/server/backend/tests/test_pushbroom_jobs.py:252-269`

**Interfaces:**
- Produces: `MENU_KEYS` 里推扫只剩 `pushbroom_workbench`、`pushbroom_mounts`；`LEGACY_MENU_KEYS` 旧键到新键的映射；`GRANT_WITH_EXECUTE = ("pushbroom_workbench",)`。

- [ ] **Step 1: 写失败的测试**

把 `test_seed_grants_pushbroom_once_to_roles_that_can_execute` 和 `test_seed_without_saved_mapping_only_records_marker` 整段替换为：

```python
def test_seed_grants_workbench_once_to_roles_that_can_execute():
    db = _SettingsDb({RBAC_KEY: {"executor": ["execute", "jobs"], "viewer": ["jobs"]}})
    assert seed_new_menus(db) == ["pushbroom_workbench"]
    saved = db.rows[RBAC_KEY].value
    assert saved["executor"] == ["execute", "jobs", "pushbroom_workbench"]
    assert saved["viewer"] == ["jobs"]
    # 管理员之后收回，再启动不会补回来
    db.rows[RBAC_KEY].value = {"executor": ["execute", "jobs"], "viewer": ["jobs"]}
    assert seed_new_menus(db) == []
    assert db.rows[SEEDED_KEY].value == {"keys": ["pushbroom_workbench"]}


def test_seed_does_not_regrant_after_old_pushbroom_menus_were_seeded():
    db = _SettingsDb(
        {RBAC_KEY: {"executor": ["execute", "jobs"]}, SEEDED_KEY: {"keys": ["pushbroom_jobs", "pushbroom_new"]}}
    )
    assert seed_new_menus(db) == []
    assert db.rows[RBAC_KEY].value["executor"] == ["execute", "jobs"]


def test_seed_without_saved_mapping_only_records_marker():
    db = _SettingsDb({})
    seed_new_menus(db)
    assert RBAC_KEY not in db.rows
    assert SEEDED_KEY in db.rows


def test_saved_old_pushbroom_keys_are_renamed():
    db = _SettingsDb(
        {RBAC_KEY: {"executor": ["execute", "pushbroom_new", "pushbroom_jobs"], "configurator": ["pushbroom_calibration"]}}
    )
    mapping = load_mapping(db)
    assert mapping["executor"].count("pushbroom_workbench") == 1
    assert "pushbroom_new" not in mapping["executor"] and "pushbroom_jobs" not in mapping["executor"]
    assert "pushbroom_mounts" in mapping["configurator"]
    assert "pushbroom_workbench" in mapping["admin"] and "pushbroom_mounts" in mapping["admin"]
```

文件顶部的 import 改为：

```python
from app.services.rbac import RBAC_KEY, SEEDED_KEY, load_mapping, seed_new_menus
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_jobs.py -k "seed or renamed"`
Expected: FAIL

- [ ] **Step 3: 实现**

`rbac.py`：

`MENU_KEYS` 里三行 `"pushbroom_new"`、`"pushbroom_jobs"`、`"pushbroom_calibration"` 换成：

```python
    "pushbroom_workbench",
    "pushbroom_mounts",
```

`DEFAULT_MENUS["executor"]` 里的 `"pushbroom_new"`、`"pushbroom_jobs"` 两行换成 `"pushbroom_workbench",`。

`ADMIN_LOCK` 下面加：

```python
# 旧版推扫菜单键。已保存的角色菜单里遇到就换成新键。
LEGACY_MENU_KEYS = {
    "pushbroom_new": "pushbroom_workbench",
    "pushbroom_jobs": "pushbroom_workbench",
    "pushbroom_calibration": "pushbroom_mounts",
}
```

`_clean_menus` 改为：

```python
def _clean_menus(keys: list[str]) -> list[str]:
    allowed = set(MENU_KEYS)
    out: list[str] = []
    for raw in keys:
        key = LEGACY_MENU_KEYS.get(raw, raw)
        if key in allowed and key not in out:
            out.append(key)
    return out
```

`GRANT_WITH_EXECUTE = ("pushbroom_new", "pushbroom_jobs")` 改为 `GRANT_WITH_EXECUTE = ("pushbroom_workbench",)`。

`seed_new_menus` 里 `done = …` 这行之后加：

```python
    # 旧版推扫菜单补发过，就当工作台也补发过，避免管理员收回后又被补上
    done |= {LEGACY_MENU_KEYS[k] for k in done if k in LEGACY_MENU_KEYS}
```

注意：标记里写回的是 `sorted(done | set(pending))`，会同时含旧键和新键，测试 `test_seed_grants_workbench_once…` 的初始标记为空，所以只含新键。

- [ ] **Step 4: 跑全部推扫和菜单测试**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_*.py tests/test_eta_and_menus.py`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add mosaic/server/backend/app/services/rbac.py mosaic/server/backend/tests/test_pushbroom_jobs.py
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图：菜单键迁移到工作台和安装配置。')"
```

---

### Task 8: 前端接口、显示工具和公共小组件

**Files:**
- Modify: `mosaic/server/frontend/src/api.ts`
- Modify: `mosaic/server/frontend/src/pages/pushbroom/labels.ts`
- Create: `mosaic/server/frontend/src/pages/pushbroom/MountTag.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/ProductHelp.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/usePreviewUrl.ts`

**Interfaces:**
- Produces（`api.ts`）：类型 `PushbroomMountSource`、`PushbroomMountRef`、`PushbroomJobBrief`、`PushbroomFlightItem`、`PushbroomMount`、`PushbroomCalibrationTask`；`PushbroomFlight.started_at`、`PushbroomJob.queue_position`、`PushbroomParams.mount_profile`；方法 `pushbroomFlights`、`pushbroomCheck`（返回 `PushbroomFlightItem`）、`pushbroomJobs(opts)`、`pushbroomMounts`、`pushbroomCreateMount`、`pushbroomUpdateMount`、`pushbroomDeleteMount`、`pushbroomSetFlightMount`、`pushbroomStartCalibration`、`pushbroomCalibrationRuns`、`pushbroomCalibrationRun`。旧的 `pushbroomCalibration`、`pushbroomCalibrate`、`pushbroomApplyCalibration` 和类型 `PushbroomCalibrationState`、`PushbroomCalibrationRun` 暂时保留，Task 10 删除。
- Produces（`labels.ts`）：`BUILTIN_MOUNT`、`MOUNT_SOURCE`、`meters`、`pixels`、`flightStartText`、`flightDay`、`mountName`、`jobMountText`、`jobMountName`、`jobElapsedSeconds`
- Produces：`<MountTag mount={PushbroomMountRef} />`、`<ProductHelp />`、`usePreviewUrl(jobId, rev) => { url, failed }`

- [ ] **Step 1: 改 `api.ts` 类型**

`PushbroomFlight` 里 `duration_s?: number;` 下面加：

```ts
  /** GPS 第一条记录的时间，UTC ISO */
  started_at?: string | null;
```

`PushbroomCheck` 后面加：

```ts
export type PushbroomMountSource = "manual" | "date" | "builtin";

/** 架次对应的安装配置。builtin 时 id、name 为空，数值是内置值。 */
export type PushbroomMountRef = {
  id: string | null;
  name: string | null;
  source: PushbroomMountSource;
  along_offset_m: number;
  boresight_px: number;
};

export type PushbroomJobBrief = {
  id: string;
  seq: number;
  status: PushbroomJob["status"];
  global_percent: number;
  current_stage: string | null;
  finished_at: string | null;
};

export type PushbroomFlightItem = PushbroomCheck & {
  mount: PushbroomMountRef;
  job_count: number;
  latest_job: PushbroomJobBrief | null;
  /** 最近一次有预览的成功任务 */
  preview_job_id: string | null;
};

export type PushbroomMount = {
  id: string;
  name: string;
  /** YYYY-MM-DD，北京时间日期 */
  effective_from: string;
  along_offset_m: number;
  along_se_m: number;
  boresight_px: number;
  boresight_se_px: number;
  flights: string[];
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  matched: number;
};
```

`PushbroomParams` 改为：

```ts
export type PushbroomParams = {
  gsd_m?: number;
  bands?: number[];
  along_offset_m?: number;
  boresight_px?: number;
  mount_profile?: { id?: string; name?: string; source: PushbroomMountSource };
};
```

`PushbroomCalibrationRun` 后面加：

```ts
export type PushbroomCalibrationTask = {
  id: string;
  status: "running" | "done" | "failed";
  input_dirs: string[];
  flights: string[];
  result: Omit<PushbroomCalibrationRun, "run_token"> | null;
  error: string | null;
  created_by: string | null;
  created_at: string | null;
  finished_at: string | null;
  /** 结果可用且不超过 30 分钟时才有 */
  run_token?: string;
  expires_at: string | null;
};
```

`PushbroomJob` 里 `has_preview: boolean;` 下面加：

```ts
  /** 排队时前面还有几个任务 */
  queue_position: number | null;
```

- [ ] **Step 2: 改 `api.ts` 方法**

`pushbroomFlights`、`pushbroomCheck`、`pushbroomJobs` 三项替换为：

```ts
  pushbroomFlights: () =>
    request<{ input_root: string; output_root: string; items: PushbroomFlightItem[] }>("/api/pushbroom/flights"),
  pushbroomCheck: (inputDir: string) =>
    request<PushbroomFlightItem>("/api/pushbroom/check", { method: "POST", body: JSON.stringify({ input_dir: inputDir }) }),
  pushbroomJobs: (opts: { page?: number; pageSize?: number; status?: string; inputDir?: string } = {}) => {
    const q = new URLSearchParams({ page: String(opts.page ?? 1), page_size: String(opts.pageSize ?? 20) });
    if (opts.status) q.set("status", opts.status);
    if (opts.inputDir) q.set("input_dir", opts.inputDir);
    return request<{ items: PushbroomJob[]; total: number; page: number; page_size: number }>(`/api/pushbroom/jobs?${q}`);
  },
```

在 `pushbroomDownload` 后面加：

```ts
  pushbroomMounts: () =>
    request<{ items: PushbroomMount[]; builtin: { along_offset_m: number; boresight_px: number } }>("/api/pushbroom/mounts"),
  pushbroomCreateMount: (body: { run_id: string; run_token: string; name: string; effective_from: string }) =>
    request<PushbroomMount>("/api/pushbroom/mounts", { method: "POST", body: JSON.stringify(body) }),
  pushbroomUpdateMount: (id: string, body: { name?: string; effective_from?: string }) =>
    request<PushbroomMount>(`/api/pushbroom/mounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  pushbroomDeleteMount: (id: string) => request<{ ok: boolean }>(`/api/pushbroom/mounts/${id}`, { method: "DELETE" }),
  pushbroomSetFlightMount: (flightName: string, profileId: string | null) =>
    request<{ ok: boolean }>(`/api/pushbroom/flights/${encodeURIComponent(flightName)}/mount`, {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    }),
  pushbroomStartCalibration: (inputDirs: string[]) =>
    request<PushbroomCalibrationTask>("/api/pushbroom/calibration/runs", {
      method: "POST",
      body: JSON.stringify({ input_dirs: inputDirs }),
    }),
  pushbroomCalibrationRuns: () => request<{ items: PushbroomCalibrationTask[] }>("/api/pushbroom/calibration/runs"),
  pushbroomCalibrationRun: (id: string) => request<PushbroomCalibrationTask>(`/api/pushbroom/calibration/runs/${id}`),
```

旧的 `PushbroomJobs.tsx` 用了 `api.pushbroomJobs(page, pageSize, statusFilter)`，签名变了；把那一处临时改成 `api.pushbroomJobs({ page, pageSize, status: statusFilter })`，Task 9 会删掉整个文件。`PushbroomNew.tsx` 的 `setFlights(data.items)`、`setSelected(await api.pushbroomCheck(path))` 类型仍然兼容（`PushbroomFlightItem` 是 `PushbroomCheck` 的超集）。

- [ ] **Step 3: 改 `labels.ts`**

第一行 import 改为：

```ts
import type { PushbroomFlight, PushbroomJob, PushbroomMountRef, PushbroomMountSource, PushbroomParams } from "../../api";
import { formatJobDateTime } from "../../jobTime";
```

文件末尾追加：

```ts
/** worker 内置的安装参数，没有对应配置时使用 */
export const BUILTIN_MOUNT = { along_offset_m: 1.7, boresight_px: -19 };

export const MOUNT_SOURCE: Record<PushbroomMountSource, string> = {
  manual: "手动",
  date: "按日期",
  builtin: "内置",
};

export function meters(value?: number | null, se?: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return se == null ? `${value.toFixed(2)} m` : `${value.toFixed(2)} ± ${se.toFixed(2)} m`;
}

export function pixels(value?: number | null, se?: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return se == null ? `${value.toFixed(1)} 像元` : `${value.toFixed(1)} ± ${se.toFixed(1)} 像元`;
}

/** 飞行开始时间，北京时间到分钟 */
export function flightStartText(f: PushbroomFlight): string {
  return f.started_at ? formatJobDateTime(f.started_at).slice(0, 16) : "—";
}

/** 飞行日期 YYYY-MM-DD（北京时间），读不到时为空串 */
export function flightDay(f: PushbroomFlight): string {
  return f.started_at ? formatJobDateTime(f.started_at).slice(0, 10) : "";
}

export function mountName(m: PushbroomMountRef): string {
  return m.source === "builtin" ? "内置（没有对应配置）" : m.name || "—";
}

/** 任务提交时抄下的安装配置，带数值 */
export function jobMountText(p: PushbroomParams): string {
  const along = p.along_offset_m ?? BUILTIN_MOUNT.along_offset_m;
  const bore = p.boresight_px ?? BUILTIN_MOUNT.boresight_px;
  const values = `沿航向 ${meters(along)} · 狭缝中心 ${pixels(bore)}`;
  const m = p.mount_profile;
  if (!m) return p.along_offset_m != null ? `旧版相机标定 · ${values}` : `内置 · ${values}`;
  if (m.source === "builtin") return `内置（没有对应配置） · ${values}`;
  return `${m.name}${m.source === "manual" ? "（手动）" : ""} · ${values}`;
}

/** 历次任务列表里的简短配置名 */
export function jobMountName(p: PushbroomParams): string {
  const m = p.mount_profile;
  if (!m) return p.along_offset_m != null ? "旧版标定" : "内置";
  return m.source === "builtin" ? "内置" : m.name || "—";
}

export function jobElapsedSeconds(job: PushbroomJob): number | null {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return (end - Date.parse(job.started_at)) / 1000;
}
```

- [ ] **Step 4: 三个公共小件**

新建 `pages/pushbroom/MountTag.tsx`：

```tsx
import { Tag } from "antd";
import type { PushbroomMountRef } from "../../api";

/** 架次对应的安装配置。没有对应配置时用橙色提醒。 */
export default function MountTag({ mount }: { mount: PushbroomMountRef }) {
  if (mount.source === "builtin") return <Tag color="orange">内置（没有对应配置）</Tag>;
  return (
    <Tag color="blue">
      {mount.name}
      {mount.source === "manual" ? " · 手动" : ""}
    </Tag>
  );
}
```

新建 `pages/pushbroom/ProductHelp.tsx`，内容是把 `PushbroomJobs.tsx` 第 39–73 行的 `PRODUCT_HELP` 和 `ProductHelp` 原样搬过来，改成默认导出：

```tsx
import { Popover, Typography } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";

const PRODUCT_HELP: { name: string; text: string }[] = [
  { name: "*_mosaic.spe", text: "拼好的高光谱立方体，ENVI BIL。数值是相机原始 DN，没有做过显示拉伸。" },
  { name: "*_mosaic.hdr", text: "立方体的头文件。行列数、波段、波长和坐标都在这里，必须和 spe 放在同一个目录。" },
  { name: "*_rgb.tif", text: "红绿蓝三个波段的 GeoTIFF，带坐标。数值仍是原始 DN，适合继续计算，直接打开会偏暗。" },
  { name: "*_rgb_display.tif", text: "同一张 RGB，做过亮度拉伸。颜色接近屏幕上的预览，用来目视，不能当作反射率。" },
  { name: "*_preview.png", text: "页面上的结果预览。内容和显示拉伸图一致，没有地理坐标。" },
  { name: "*_footprints.geojson", text: "每条航带落到成果上的范围，可在 GIS 里查看航带是怎么拼上去的。" },
  { name: "report.json", text: "处理报告，给程序读。里面有分辨率、覆盖面积、配准、标定提示和各步耗时。" },
  { name: "report.md", text: "同一份报告的文字版，用文本编辑器直接打开。" },
];

export default function ProductHelp() {
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      title="成果文件说明"
      getPopupContainer={() => document.body}
      content={
        <div style={{ width: 380 }}>
          {PRODUCT_HELP.map((item) => (
            <div key={item.name} style={{ marginBottom: 10 }}>
              <Typography.Text code>{item.name}</Typography.Text>
              <div style={{ marginTop: 2 }}>{item.text}</div>
            </div>
          ))}
        </div>
      }
    >
      <button type="button" className="param-help-btn" aria-label="成果文件说明" onClick={(e) => e.stopPropagation()}>
        <QuestionCircleOutlined />
      </button>
    </Popover>
  );
}
```

新建 `pages/pushbroom/usePreviewUrl.ts`：

```ts
import { useEffect, useState } from "react";
import { api } from "../../api";

/** 结果预览要带登录头，取成 blob 地址。换任务或卸载时回收。 */
export function usePreviewUrl(jobId: string | null | undefined, rev?: string | null) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!jobId) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    api
      .pushbroomPreview(jobId, rev)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, rev]);

  return { url, failed };
}
```

- [ ] **Step 5: 类型检查和打包**

Run: `cd mosaic/server/frontend && npx tsc --noEmit -p tsconfig.json && npm run build`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add mosaic/server/frontend/src/api.ts mosaic/server/frontend/src/pages/pushbroom/labels.ts \
  mosaic/server/frontend/src/pages/pushbroom/MountTag.tsx mosaic/server/frontend/src/pages/pushbroom/ProductHelp.tsx \
  mosaic/server/frontend/src/pages/pushbroom/usePreviewUrl.ts
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图前端：安装配置和后台标定的接口、显示工具。')"
```

`PushbroomJobs.tsx` 这一处临时改动不单独提交，留给 Task 9 连同删除一起处理；如果 Task 8 的打包因它失败，就把它也 `git add` 进来（前提是开工前用户已经处理了这个文件上别人的改动）。

---

### Task 9: 工作台页面

**Files:**
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/jobActions.ts`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/FlightList.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/FlightHeader.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/JobHistory.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/JobMain.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/JobTabs.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/workbench/PushbroomWorkbench.tsx`
- Delete: `mosaic/server/frontend/src/pages/pushbroom/PushbroomNew.tsx`、`PushbroomJobs.tsx`
- Modify: `mosaic/server/frontend/src/roles.ts`、`components/AppShell.tsx`、`rbac.tsx`、`theme.css`

**Interfaces:**
- Consumes: Task 8 的全部导出；`components/ImageStage.tsx` 的 `ImageInspectModal({ open, src, title, onClose })`；`jobTime.ts` 的 `formatJobDateTime`；`ids.ts` 的 `formatNo`；`auth.tsx` 的 `useAuth().roles`
- Produces：路由 `/pushbroom`（工作台）；地址参数 `flight`（架次目录名）、`job`（任务 id）

- [ ] **Step 1: 任务操作**

新建 `workbench/jobActions.ts`：

```ts
import { createElement } from "react";
import { Checkbox, Flex, Modal, Typography, message } from "antd";
import { api, type PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";

async function run(fn: () => Promise<unknown>, ok: string, after: () => void) {
  try {
    await fn();
    message.success(ok);
    after();
  } catch (err) {
    message.error((err as Error).message || String(err));
  }
}

export function retryJob(job: PushbroomJob, after: () => void) {
  return run(() => api.pushbroomRetry(job.id), "已重新提交", after);
}

export function confirmCancel(job: PushbroomJob, after: () => void) {
  Modal.confirm({
    title: `取消推扫任务 ${formatNo(job.seq)}？`,
    content: job.status === "running" ? "正在计算的进程会被停止，已写出的中间文件保留在成果目录。" : "任务还在排队，取消后不会开始计算。",
    okText: "取消任务",
    okButtonProps: { danger: true },
    cancelText: "返回",
    onOk: () => run(() => api.pushbroomCancel(job.id), "已发出取消", after),
  });
}

export function confirmDelete(job: PushbroomJob, after: () => void) {
  let withFiles = false;
  Modal.confirm({
    title: `删除推扫任务 ${formatNo(job.seq)}？`,
    content: createElement(
      Flex,
      { vertical: true, gap: 8 },
      createElement("span", null, "删除任务记录和日志。"),
      createElement(
        Checkbox,
        { onChange: (e) => (withFiles = e.target.checked) },
        "同时删除成果目录 ",
        createElement(Typography.Text, { code: true }, job.output_dir),
      ),
    ),
    okText: "删除",
    okButtonProps: { danger: true },
    cancelText: "返回",
    onOk: () =>
      run(() => api.pushbroomDelete(job.id, withFiles), withFiles ? "任务和成果已删除" : "任务记录已删除，成果文件保留", after),
  });
}
```

- [ ] **Step 2: 左栏架次列表**

新建 `workbench/FlightList.tsx`：

```tsx
import { useMemo, useState } from "react";
import { Button, Empty, Input, Progress, Segmented, Space, Tag, Typography, message } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { api, type PushbroomFlightItem } from "../../../api";
import MountTag from "../MountTag";
import { PB_ACTIVE, PB_STATUS, flightStartText, formatBytes } from "../labels";
import { usePreviewUrl } from "../usePreviewUrl";

type Filter = "all" | "active" | "never" | "bad";

function isActive(item: PushbroomFlightItem) {
  return Boolean(item.latest_job && PB_ACTIVE.includes(item.latest_job.status));
}

function matches(item: PushbroomFlightItem, filter: Filter) {
  if (filter === "active") return isActive(item);
  if (filter === "never") return item.ok && item.job_count === 0;
  if (filter === "bad") return !item.ok;
  return true;
}

function Thumb({ item }: { item: PushbroomFlightItem }) {
  const { url } = usePreviewUrl(item.preview_job_id, item.latest_job?.finished_at);
  if (!url) return <span className="pb-flight-thumb pb-flight-thumb--empty" aria-hidden />;
  return <img className="pb-flight-thumb" src={url} alt={`${item.name} 最近结果`} />;
}

function FlightCard({ item, active, onSelect }: { item: PushbroomFlightItem; active: boolean; onSelect: () => void }) {
  const latest = item.latest_job;
  const status = latest ? PB_STATUS[latest.status] : null;
  const pick = () => item.ok && onSelect();
  return (
    <div
      className={`pb-flight-card${active ? " is-active" : ""}${item.ok ? "" : " is-bad"}`}
      role="button"
      tabIndex={item.ok ? 0 : -1}
      aria-disabled={!item.ok}
      aria-pressed={active}
      onClick={pick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          pick();
        }
      }}
    >
      <Thumb item={item} />
      <div className="pb-flight-body">
        <div className="pb-flight-title">
          <Typography.Text strong ellipsis={{ tooltip: item.name }}>
            {item.name}
          </Typography.Text>
          {status && <Tag color={status.color}>{status.text}</Tag>}
        </div>
        <Typography.Text type="secondary" className="pb-flight-meta">
          {flightStartText(item.flight)} · {formatBytes(item.flight.spe_bytes)}
        </Typography.Text>
        {item.ok ? (
          <div className="pb-flight-meta">
            <MountTag mount={item.mount} />
            <Typography.Text type="secondary">{item.job_count ? `跑过 ${item.job_count} 次` : "未跑过"}</Typography.Text>
          </div>
        ) : (
          <Typography.Text type="danger" className="pb-flight-meta">
            {item.errors[0] || "核对未通过"}
          </Typography.Text>
        )}
        {latest && isActive(item) && <Progress percent={Math.round(latest.global_percent || 0)} size="small" status="active" />}
      </div>
    </div>
  );
}

export default function FlightList({
  items,
  selected,
  loading,
  onSelect,
  onReload,
  onChecked,
}: {
  items: PushbroomFlightItem[];
  selected: string | null;
  loading: boolean;
  onSelect: (name: string) => void;
  onReload: () => void;
  onChecked: (item: PushbroomFlightItem) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [keyword, setKeyword] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [manualError, setManualError] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);

  const counts = useMemo(
    () => ({
      all: items.length,
      active: items.filter((i) => matches(i, "active")).length,
      never: items.filter((i) => matches(i, "never")).length,
      bad: items.filter((i) => matches(i, "bad")).length,
    }),
    [items],
  );
  const shown = items.filter((i) => matches(i, filter) && i.name.toLowerCase().includes(keyword.trim().toLowerCase()));

  async function checkManual() {
    const path = manualPath.trim();
    if (!path) {
      message.warning("请填写架次目录");
      return;
    }
    setChecking(true);
    setManualError([]);
    try {
      const item = await api.pushbroomCheck(path);
      if (item.ok) {
        onChecked(item);
        setManualOpen(false);
        setManualPath("");
      } else {
        setManualError(item.errors.length ? item.errors : ["核对未通过"]);
      }
    } catch (err) {
      setManualError([(err as Error).message || String(err)]);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="pb-flight-list">
      <div className="pb-flight-tools">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索架次"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onReload} aria-label="刷新架次" />
      </div>
      <Segmented<Filter>
        block
        size="small"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "all", label: `全部 ${counts.all}` },
          { value: "active", label: `运行中 ${counts.active}` },
          { value: "never", label: `未跑过 ${counts.never}` },
          { value: "bad", label: `不可用 ${counts.bad}` },
        ]}
      />
      <div className="pb-flight-scroll">
        {shown.map((item) => (
          <FlightCard key={item.input_dir} item={item} active={item.name === selected} onSelect={() => onSelect(item.name)} />
        ))}
        {!loading && shown.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的架次" />}
      </div>
      <div className="pb-flight-manual">
        <Button type="link" style={{ padding: 0 }} onClick={() => setManualOpen((v) => !v)}>
          {manualOpen ? "收起目录输入" : "列表里没有？填写目录"}
        </Button>
        {manualOpen && (
          <>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                placeholder="/data/input/pushbroom/FigSpec_xxx"
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                onPressEnter={() => checkManual()}
              />
              <Button onClick={() => checkManual()} loading={checking}>
                核对
              </Button>
            </Space.Compact>
            {manualError.map((line) => (
              <Typography.Text key={line} type="danger" className="pb-flight-meta">
                {line}
              </Typography.Text>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 右侧顶部**

新建 `workbench/FlightHeader.tsx`：

```tsx
import { useState } from "react";
import { Alert, Button, Card, Flex, Input, InputNumber, Modal, Popover, Radio, Select, Typography, message } from "antd";
import { DownOutlined } from "@ant-design/icons";
import { ApiError, api, type PushbroomFlightItem, type PushbroomJob, type PushbroomMount } from "../../../api";
import MountTag from "../MountTag";
import { flightSize, flightStartText, formatBytes, formatDuration, meters, parseBandList, pixels } from "../labels";

const AUTO = "__auto__";

function MountPicker({
  flight,
  open,
  onClose,
  onDone,
}: {
  flight: PushbroomFlightItem;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [mounts, setMounts] = useState<PushbroomMount[] | null>(null);
  const [value, setValue] = useState<string>(flight.mount.source === "manual" && flight.mount.id ? flight.mount.id : AUTO);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const data = await api.pushbroomMounts();
      setMounts([...data.items].sort((a, b) => b.effective_from.localeCompare(a.effective_from)));
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  async function save() {
    setSaving(true);
    try {
      await api.pushbroomSetFlightMount(flight.name, value === AUTO ? null : value);
      message.success(value === AUTO ? "已改回按日期自动选择" : "已手动指定");
      onDone();
      onClose();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`${flight.name} 用哪组安装配置`}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      onOk={save}
      onCancel={onClose}
      afterOpenChange={(visible) => {
        if (visible && mounts === null) load();
      }}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">只影响之后提交的任务。已提交的任务保留各自的参数。</Typography.Text>
        <Select
          value={value}
          onChange={setValue}
          loading={mounts === null}
          options={[
            { value: AUTO, label: "按飞行日期自动选择" },
            ...(mounts || []).map((m) => ({
              value: m.id,
              label: `${m.name}（${m.effective_from} 起） · ${meters(m.along_offset_m)} · ${pixels(m.boresight_px)}`,
            })),
          ]}
        />
      </Flex>
    </Modal>
  );
}

export default function FlightHeader({
  flight,
  admin,
  onSubmitted,
  onMountChanged,
}: {
  flight: PushbroomFlightItem;
  admin: boolean;
  onSubmitted: (job: PushbroomJob) => void;
  onMountChanged: () => void;
}) {
  // 父组件按架次给 key，换架次时这里整体重建，选项自然回到默认
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [gsdMode, setGsdMode] = useState<"auto" | "manual">("auto");
  const [gsdCm, setGsdCm] = useState<number | null>(null);
  const [bandMode, setBandMode] = useState<"all" | "pick">("all");
  const [bandsText, setBandsText] = useState("");
  const [optionError, setOptionError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const f = flight.flight;
  const summary = `${gsdMode === "manual" && gsdCm ? `分辨率 ${gsdCm} cm` : "分辨率自动"} · ${
    bandMode === "pick" && bandsText.trim() ? `波段 ${bandsText.trim()}` : "全部波段"
  }${title.trim() ? ` · ${title.trim()}` : ""}`;

  async function submit() {
    setOptionError("");
    let bands: number[] | undefined;
    if (bandMode === "pick") {
      try {
        bands = parseBandList(bandsText, f.bands);
      } catch (err) {
        setOptionError((err as Error).message);
        setOptionsOpen(true);
        return;
      }
    }
    if (gsdMode === "manual" && !gsdCm) {
      setOptionError("请填写分辨率");
      setOptionsOpen(true);
      return;
    }
    setSubmitting(true);
    try {
      const job = await api.pushbroomCreate({
        input_dir: flight.input_dir,
        title: title.trim() || undefined,
        params: {
          ...(gsdMode === "manual" && gsdCm ? { gsd_m: gsdCm / 100 } : {}),
          ...(bands ? { bands } : {}),
        },
      });
      message.success(`推扫任务 #${job.seq} 已提交`);
      setOptionsOpen(false);
      onSubmitted(job);
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { check?: { errors?: string[] } } | undefined) : undefined;
      message.error(detail?.check?.errors?.[0] || (err as Error).message || String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const options = (
    <Flex vertical gap={12} style={{ width: 340 }}>
      <div>
        <Typography.Text>任务名称</Typography.Text>
        <Input maxLength={120} placeholder={`不填用 ${flight.name}`} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div>
        <Typography.Text>成果分辨率</Typography.Text>
        <Radio.Group value={gsdMode} onChange={(e) => setGsdMode(e.target.value)} style={{ display: "block" }}>
          <Radio value="auto">自动（按航高和相机估算）</Radio>
          <Radio value="manual">指定</Radio>
        </Radio.Group>
        {gsdMode === "manual" && (
          <InputNumber min={1} max={200} step={0.5} suffix="cm" value={gsdCm} onChange={setGsdCm} style={{ width: 160, marginTop: 6 }} />
        )}
      </div>
      <div>
        <Typography.Text>输出波段</Typography.Text>
        <Radio.Group value={bandMode} onChange={(e) => setBandMode(e.target.value)} style={{ display: "block" }}>
          <Radio value="all">全部波段</Radio>
          <Radio value="pick">指定波段</Radio>
        </Radio.Group>
        {bandMode === "pick" && (
          <Input
            style={{ marginTop: 6 }}
            placeholder={`例如 48, 150, 208${f.bands ? `（共 ${f.bands} 个）` : ""}`}
            value={bandsText}
            onChange={(e) => setBandsText(e.target.value)}
          />
        )}
      </div>
      {optionError && <Typography.Text type="danger">{optionError}</Typography.Text>}
    </Flex>
  );

  return (
    <Card className="mosaic-panel pb-wb-head">
      <Flex justify="space-between" align="flex-start" gap={16} wrap>
        <Flex vertical gap={6} style={{ minWidth: 0, flex: 1 }}>
          <Typography.Title level={4} style={{ margin: 0 }} ellipsis={{ tooltip: flight.name }}>
            {flight.name}
          </Typography.Title>
          <Typography.Text type="secondary">
            {flightStartText(f)} 起飞 · 飞行 {formatDuration(f.duration_s)} · {flightSize(f)} · {formatBytes(f.spe_bytes)}
          </Typography.Text>
          <Flex gap={8} align="center" wrap>
            <Typography.Text type="secondary">安装配置</Typography.Text>
            <MountTag mount={flight.mount} />
            <Typography.Text type="secondary">
              沿航向 {meters(flight.mount.along_offset_m)} · 狭缝中心 {pixels(flight.mount.boresight_px)}
            </Typography.Text>
            {admin && (
              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setPickerOpen(true)}>
                改选
              </Button>
            )}
          </Flex>
        </Flex>
        <Flex vertical align="flex-end" gap={6}>
          <Flex gap={8}>
            <Popover trigger="click" placement="bottomRight" open={optionsOpen} onOpenChange={setOptionsOpen} content={options}>
              <Button>
                选项 <DownOutlined />
              </Button>
            </Popover>
            <Button type="primary" loading={submitting} onClick={submit}>
              提交拼图
            </Button>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {summary}
          </Typography.Text>
        </Flex>
      </Flex>
      {flight.warnings.length > 0 && (
        <Alert style={{ marginTop: 12 }} type="warning" showIcon message={flight.warnings.join("；")} />
      )}
      {admin && (
        <MountPicker flight={flight} open={pickerOpen} onClose={() => setPickerOpen(false)} onDone={onMountChanged} />
      )}
    </Card>
  );
}
```

- [ ] **Step 4: 历次任务**

新建 `workbench/JobHistory.tsx`：

```tsx
import { Button, Dropdown, Empty, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { EllipsisOutlined } from "@ant-design/icons";
import type { PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import { formatJobDateTime } from "../../../jobTime";
import { PB_ACTIVE, PB_STATUS, formatDuration, jobElapsedSeconds, jobMountName } from "../labels";
import { confirmCancel, confirmDelete, retryJob } from "./jobActions";

function menuFor(job: PushbroomJob): MenuProps["items"] {
  const items: NonNullable<MenuProps["items"]> = [];
  if (job.status === "failed" || job.status === "cancelled") items.push({ key: "retry", label: "重跑" });
  if (job.status === "queued" || job.status === "running") items.push({ key: "cancel", label: "取消", danger: true });
  if (!PB_ACTIVE.includes(job.status)) items.push({ key: "delete", label: "删除", danger: true });
  return items;
}

export default function JobHistory({
  jobs,
  selectedId,
  onSelect,
  onChanged,
}: {
  jobs: PushbroomJob[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  if (!jobs.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有任务" />;
  return (
    <div className="pb-history">
      {jobs.map((job) => {
        const s = PB_STATUS[job.status];
        const items = menuFor(job);
        return (
          <div
            key={job.id}
            className={`pb-history-item${job.id === selectedId ? " is-active" : ""}`}
            role="button"
            tabIndex={0}
            aria-pressed={job.id === selectedId}
            onClick={() => onSelect(job.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(job.id);
              }
            }}
          >
            <div className="pb-history-row">
              <Typography.Text strong>{formatNo(job.seq)}</Typography.Text>
              <Tag color={s.color}>{s.text}</Tag>
              {items && items.length > 0 && (
                <Dropdown
                  trigger={["click"]}
                  menu={{
                    items,
                    onClick: ({ key, domEvent }) => {
                      domEvent.stopPropagation();
                      if (key === "retry") retryJob(job, onChanged);
                      if (key === "cancel") confirmCancel(job, onChanged);
                      if (key === "delete") confirmDelete(job, onChanged);
                    },
                  }}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={<EllipsisOutlined />}
                    aria-label={`任务 ${formatNo(job.seq)} 的操作`}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Dropdown>
              )}
            </div>
            <Typography.Text type="secondary" className="pb-history-meta">
              {formatJobDateTime(job.created_at).slice(0, 16)} · {formatDuration(jobElapsedSeconds(job))}
            </Typography.Text>
            <Typography.Text type="secondary" className="pb-history-meta">
              {jobMountName(job.params)}
              {job.run_attempt > 1 ? ` · 第 ${job.run_attempt} 次运行` : ""}
            </Typography.Text>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 5: 主区域**

新建 `workbench/JobMain.tsx`：

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Empty, Flex, Progress, Spin, Steps, Typography, message } from "antd";
import { DownloadOutlined, ExpandOutlined } from "@ant-design/icons";
import { api, type PushbroomJob } from "../../../api";
import { ImageInspectModal } from "../../../components/ImageStage";
import { formatNo } from "../../../ids";
import { PB_STAGES, stageLabel } from "../labels";
import { usePreviewUrl } from "../usePreviewUrl";
import { confirmCancel, retryJob } from "./jobActions";

function ResultPanel({ job }: { job: PushbroomJob }) {
  const { url, failed } = usePreviewUrl(job.has_preview ? job.id : null, job.finished_at || job.updated_at);
  const [open, setOpen] = useState(false);
  const [packing, setPacking] = useState(false);
  const title = `${formatNo(job.seq)} ${job.title} 结果预览`;

  async function pack() {
    setPacking(true);
    try {
      await api.pushbroomDownloadArchive(job.id);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setPacking(false);
    }
  }

  return (
    <Flex vertical gap={12} className="pb-result">
      <Flex justify="space-between" align="center">
        <Typography.Text strong>结果预览</Typography.Text>
        <Flex gap={8}>
          <Button icon={<ExpandOutlined />} disabled={!url} onClick={() => setOpen(true)}>
            全屏查看
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} loading={packing} onClick={pack}>
            打包下载
          </Button>
        </Flex>
      </Flex>
      <div className="pb-preview-box">
        {url ? (
          <img
            src={url}
            alt={title}
            role="button"
            tabIndex={0}
            onClick={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setOpen(true);
            }}
          />
        ) : failed ? (
          <Typography.Text type="secondary">预览加载失败</Typography.Text>
        ) : job.has_preview ? (
          <Spin />
        ) : (
          <Typography.Text type="secondary">这次没有生成预览，成果文件在下方</Typography.Text>
        )}
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        点图或「全屏查看」可以滚轮缩放、按住拖动。
      </Typography.Text>
      <ImageInspectModal open={open} src={url} title={title} onClose={() => setOpen(false)} />
    </Flex>
  );
}

function RunningPanel({ job, onChanged }: { job: PushbroomJob; onChanged: () => void }) {
  const index = Math.max(0, PB_STAGES.findIndex((s) => s.id === job.current_stage));
  return (
    <Flex vertical gap={16} className="pb-running">
      <Flex justify="space-between" align="center">
        <Typography.Text strong>{job.status === "cancelling" ? "正在取消" : `正在计算：${stageLabel(job.current_stage)}`}</Typography.Text>
        {job.status === "running" && (
          <Button danger onClick={() => confirmCancel(job, onChanged)}>
            取消
          </Button>
        )}
      </Flex>
      <Steps
        size="small"
        labelPlacement="vertical"
        current={index}
        percent={Math.round(job.stage_progress || 0)}
        items={PB_STAGES.map((s) => ({ title: s.label }))}
      />
      <Progress percent={Math.round(job.global_percent || 0)} status="active" />
      {job.message && <Typography.Text type="secondary">{job.message}</Typography.Text>}
    </Flex>
  );
}

export default function JobMain({
  job,
  admin,
  flightName,
  onChanged,
}: {
  job: PushbroomJob | null;
  admin: boolean;
  flightName: string;
  onChanged: () => void;
}) {
  if (!job) {
    return <Empty description="这个架次还没跑过。点右上角「提交拼图」开始。" />;
  }
  const warning = job.calibration_warning ? (
    <Alert
      type="warning"
      showIcon
      message="本架次与所用安装配置不符，航带接缝可能错开。"
      description={
        admin ? (
          <Link to={`/pushbroom/mounts?flight=${encodeURIComponent(flightName)}`}>去安装配置看看这个架次</Link>
        ) : (
          job.calibration_warning
        )
      }
    />
  ) : null;

  let body;
  if (job.status === "queued") {
    const n = job.queue_position ?? 0;
    body = (
      <Flex vertical align="center" gap={12} className="pb-state">
        <Typography.Title level={5} style={{ margin: 0 }}>
          排队中
        </Typography.Title>
        <Typography.Text type="secondary">{n > 0 ? `前面还有 ${n} 个任务` : "下一个就轮到它"}</Typography.Text>
        <Button danger onClick={() => confirmCancel(job, onChanged)}>
          取消
        </Button>
      </Flex>
    );
  } else if (job.status === "running" || job.status === "cancelling") {
    body = <RunningPanel job={job} onChanged={onChanged} />;
  } else if (job.status === "succeeded") {
    body = <ResultPanel job={job} />;
  } else if (job.status === "failed") {
    body = (
      <Flex vertical gap={12}>
        <Alert type="error" showIcon message="失败原因" description={job.error_summary || "没有记录原因，请看运行日志"} />
        <div>
          <Button type="primary" onClick={() => retryJob(job, onChanged)}>
            重跑
          </Button>
        </div>
      </Flex>
    );
  } else {
    body = (
      <Flex vertical align="center" gap={12} className="pb-state">
        <Typography.Title level={5} style={{ margin: 0 }}>
          已取消
        </Typography.Title>
        <Button type="primary" onClick={() => retryJob(job, onChanged)}>
          重跑
        </Button>
      </Flex>
    );
  }
  return (
    <Flex vertical gap={12}>
      {warning}
      {body}
    </Flex>
  );
}
```

- [ ] **Step 6: 底部页签**

新建 `workbench/JobTabs.tsx`：

```tsx
import { useEffect, useRef, useState } from "react";
import { Button, Descriptions, Table, Tabs, Tag, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { api, type PushbroomFile, type PushbroomJob } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import ProductHelp from "../ProductHelp";
import {
  PB_ACTIVE,
  PB_STAGES,
  flightSize,
  formatBandList,
  formatBytes,
  formatDuration,
  jobElapsedSeconds,
  jobMountText,
} from "../labels";

export default function JobTabs({ job }: { job: PushbroomJob }) {
  const [files, setFiles] = useState<PushbroomFile[]>([]);
  const [logs, setLogs] = useState("");
  const logRef = useRef<HTMLPreElement>(null);
  const active = PB_ACTIVE.includes(job.status);

  useEffect(() => {
    let cancelled = false;
    api
      .pushbroomFiles(job.id)
      .then((r) => !cancelled && setFiles(r.items))
      .catch(() => !cancelled && setFiles([]));
    return () => {
      cancelled = true;
    };
  }, [job.id, job.status]);

  useEffect(() => {
    let cancelled = false;
    api
      .pushbroomLogs(job.id)
      .then((text) => !cancelled && setLogs(text))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [job.id, job.updated_at]);

  useEffect(() => {
    const el = logRef.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [logs, active]);

  async function download(f: PushbroomFile) {
    try {
      await api.pushbroomDownload(job.id, f.path);
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  return (
    <Tabs
      items={[
        {
          key: "files",
          label: (
            <span className="title-with-help">
              成果文件
              <ProductHelp />
            </span>
          ),
          children: (
            <Table
              rowKey="path"
              size="small"
              dataSource={files}
              pagination={false}
              locale={{ emptyText: job.status === "succeeded" ? "成果目录为空" : "完成后在这里下载" }}
              columns={[
                { title: "文件", dataIndex: "name", ellipsis: true },
                { title: "类别", dataIndex: "kind", width: 90, render: (k: string) => (k === "report" ? "报告" : "成果") },
                { title: "大小", dataIndex: "size", width: 100, render: (n: number) => formatBytes(n) },
                {
                  title: "",
                  width: 90,
                  render: (_: unknown, f: PushbroomFile) => (
                    <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => download(f)}>
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          ),
        },
        {
          key: "params",
          label: "处理参数",
          children: (
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="架次目录" span={2}>
                <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                  {job.input_dir}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="成果目录" span={2}>
                <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                  {job.output_dir}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="安装配置" span={2}>
                {jobMountText(job.params)}
              </Descriptions.Item>
              <Descriptions.Item label="分辨率">
                {job.params.gsd_m ? `${(job.params.gsd_m * 100).toFixed(1)} cm` : "自动"}
              </Descriptions.Item>
              <Descriptions.Item label="输出波段">{formatBandList(job.params.bands)}</Descriptions.Item>
              <Descriptions.Item label="影像尺寸">{flightSize(job.flight)}</Descriptions.Item>
              <Descriptions.Item label="原始数据">{formatBytes(job.flight.spe_bytes)}</Descriptions.Item>
              <Descriptions.Item label="提交人">{job.created_by || "—"}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{formatJobDateTime(job.created_at)}</Descriptions.Item>
              <Descriptions.Item label="运行次数">第 {job.run_attempt} 次</Descriptions.Item>
              <Descriptions.Item label="耗时">{formatDuration(jobElapsedSeconds(job))}</Descriptions.Item>
            </Descriptions>
          ),
        },
        {
          key: "timings",
          label: "各步耗时",
          children: (
            <div className="pb-timings">
              {PB_STAGES.map((s) => {
                const sec = job.stage_timings[s.id];
                const current = job.current_stage === s.id && active;
                return (
                  <Tag key={s.id} color={sec != null ? "success" : current ? "processing" : "default"}>
                    {s.label}
                    {sec != null ? ` ${formatDuration(sec)}` : current ? " 进行中" : ""}
                  </Tag>
                );
              })}
            </div>
          ),
        },
        {
          key: "logs",
          label: "运行日志",
          children: (
            <pre ref={logRef} className="pb-logs">
              {logs || "(暂无日志)"}
            </pre>
          ),
        },
      ]}
    />
  );
}
```

- [ ] **Step 7: 页面**

新建 `workbench/PushbroomWorkbench.tsx`：

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Card, Empty, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomJob, type PushbroomStatus } from "../../../api";
import { useAuth } from "../../../auth";
import { PB_ACTIVE } from "../labels";
import FlightHeader from "./FlightHeader";
import FlightList from "./FlightList";
import JobHistory from "./JobHistory";
import JobMain from "./JobMain";
import JobTabs from "./JobTabs";

const POLL_MS = 3000;

function baseName(path: string) {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

export default function PushbroomWorkbenchPage() {
  const { roles } = useAuth();
  const admin = roles.includes("admin");
  const [params, setParams] = useSearchParams();
  const flightName = params.get("flight");
  const jobParam = params.get("job");
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [flights, setFlights] = useState<PushbroomFlightItem[]>([]);
  const [extra, setExtra] = useState<PushbroomFlightItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState<PushbroomJob[]>([]);
  const [detail, setDetail] = useState<PushbroomJob | null>(null);

  const all = useMemo(
    () => [...flights, ...extra.filter((e) => !flights.some((f) => f.input_dir === e.input_dir))],
    [flights, extra],
  );
  const flight = all.find((f) => f.name === flightName && f.ok) ?? null;
  const selectedId = jobs.some((j) => j.id === jobParam) ? jobParam : (jobs[0]?.id ?? null);
  const shown = (detail?.id === selectedId ? detail : jobs.find((j) => j.id === selectedId)) ?? null;

  const select = useCallback(
    (next: { flight?: string | null; job?: string | null }) => {
      setParams(
        (cur) => {
          const q = new URLSearchParams(cur);
          for (const [key, value] of Object.entries(next)) {
            if (value) q.set(key, value);
            else q.delete(key);
          }
          return q;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const loadFlights = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [st, data] = await Promise.all([api.pushbroomStatus(), api.pushbroomFlights()]);
      setStatus(st);
      setFlights(data.items);
    } catch (err) {
      if (!quiet) message.error((err as Error).message || String(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    if (!flight) {
      setJobs([]);
      return;
    }
    const data = await api.pushbroomJobs({ inputDir: flight.input_dir, pageSize: 50 });
    setJobs(data.items);
  }, [flight?.input_dir]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadDetail = useCallback(async () => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetail(await api.pushbroomJob(selectedId));
  }, [selectedId]);

  const refresh = useCallback(() => {
    loadFlights(true).catch(() => undefined);
    loadJobs().catch(() => undefined);
    loadDetail().catch(() => undefined);
  }, [loadFlights, loadJobs, loadDetail]);

  useEffect(() => {
    loadFlights().catch(() => undefined);
  }, [loadFlights]);

  useEffect(() => {
    setDetail(null);
    loadJobs().catch((err) => message.error((err as Error).message || String(err)));
  }, [loadJobs]);

  useEffect(() => {
    loadDetail().catch(() => undefined);
  }, [loadDetail]);

  // 只有任务链接时，按任务找到架次补进地址
  useEffect(() => {
    if (flightName || !jobParam) return;
    api
      .pushbroomJob(jobParam)
      .then((j) => select({ flight: baseName(j.input_dir), job: j.id }))
      .catch(() => select({ job: null }));
  }, [flightName, jobParam, select]);

  const live =
    flights.some((f) => f.latest_job && PB_ACTIVE.includes(f.latest_job.status)) || jobs.some((j) => PB_ACTIVE.includes(j.status));
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [live, refresh]);

  function onSubmitted(job: PushbroomJob) {
    setJobs((cur) => [job, ...cur.filter((j) => j.id !== job.id)]);
    select({ job: job.id });
    loadFlights(true).catch(() => undefined);
  }

  function onChecked(item: PushbroomFlightItem) {
    setExtra((cur) => [...cur.filter((e) => e.input_dir !== item.input_dir), item]);
    select({ flight: item.name, job: null });
  }

  return (
    <div className="pb-wb">
      {status?.worker_online === false && (
        <Alert
          type="warning"
          showIcon
          message="推扫计算服务未在线"
          description="任务可以提交，会在队列里等待。请联系管理员启动推扫计算服务。"
        />
      )}
      <div className="pb-wb-body">
        <aside className="pb-wb-side mosaic-panel">
          <FlightList
            items={all}
            selected={flight?.name ?? null}
            loading={loading}
            onSelect={(name) => select({ flight: name, job: null })}
            onReload={() => loadFlights().catch(() => undefined)}
            onChecked={onChecked}
          />
        </aside>
        <section className="pb-wb-main">
          {!flight ? (
            <Card className="mosaic-panel pb-wb-empty">
              <Empty description="在左边选一个架次" />
            </Card>
          ) : (
            <>
              <FlightHeader
                key={flight.input_dir}
                flight={flight}
                admin={admin}
                onSubmitted={onSubmitted}
                onMountChanged={() => loadFlights(true).catch(() => undefined)}
              />
              <div className="pb-wb-mid">
                <Card className="mosaic-panel pb-wb-stage">
                  <JobMain job={shown} admin={admin} flightName={flight.name} onChanged={refresh} />
                </Card>
                <Card className="mosaic-panel pb-wb-history" title={`历次任务（${jobs.length}）`}>
                  <JobHistory jobs={jobs} selectedId={selectedId} onSelect={(id) => select({ job: id })} onChanged={refresh} />
                </Card>
              </div>
              {shown && (
                <Card className="mosaic-panel">
                  <JobTabs job={shown} />
                </Card>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
```

项目没有装 eslint 插件；`// eslint-disable-line` 那一行注释去掉，写成 `}, [flight?.input_dir]);` 即可（`flight` 是渲染时算出来的，按目录变化重新取历次任务）。TypeScript 不检查 hooks 依赖。

- [ ] **Step 8: 菜单、路由、开关缓存**

`roles.ts`：

`MenuKey` 里三行换成：

```ts
  | "pushbroom_workbench"
  | "pushbroom_mounts"
```

`MENU_CATALOG` 里三行推扫菜单换成：

```ts
  { key: "pushbroom_workbench", label: "推扫拼图", path: "/pushbroom", group: "推扫拼图", feature: "pushbroom", roles: ["admin", "executor"], hint: "选架次、提交拼图、看进度和结果、下载成果" },
  { key: "pushbroom_mounts", label: "安装配置", path: "/pushbroom/mounts", group: "推扫拼图", feature: "pushbroom", roles: ["admin"], hint: "按生效日期管理相机安装配置，新建时用往返航带标定" },
```

`components/AppShell.tsx`：

- 删掉 `PushbroomNewPage`、`PushbroomJobsPage` 两个 import，加 `import PushbroomWorkbenchPage from "../pages/pushbroom/workbench/PushbroomWorkbench";`。本任务先保留 `PushbroomCalibrationPage` 的 import。
- 图标 import 里 `PlusCircleOutlined`、`HistoryOutlined` 换成 `ScanOutlined` 已有，不用新增；删掉这两个。
- `TITLE` 里三行换成：
  ```ts
  pushbroom_workbench: "推扫拼图",
  pushbroom_mounts: "安装配置",
  ```
- `MENU_ICON` 里三行换成：
  ```ts
  pushbroom_workbench: <ScanOutlined />,
  pushbroom_mounts: <AimOutlined />,
  ```
- 第 166–167 行换成：
  ```ts
  // 推扫：/pushbroom 是工作台，/pushbroom/mounts 是安装配置
  const key = top === "pushbroom" ? (loc.pathname.split("/")[2] === "mounts" ? "pushbroom_mounts" : "pushbroom_workbench") : top;
  ```
- `/pushbroom/new`、`/pushbroom/jobs`、`/pushbroom/calibration` 三个 `<Route>` 换成：
  ```tsx
  <Route
    path="/pushbroom"
    element={
      <FeatureGuard allow={canMenu("pushbroom_workbench")}>
        <PushbroomWorkbenchPage />
      </FeatureGuard>
    }
  />
  <Route path="/pushbroom/new" element={<Navigate to="/pushbroom" replace />} />
  <Route path="/pushbroom/jobs" element={<LegacyPushbroomJobs />} />
  <Route
    path="/pushbroom/mounts"
    element={
      <FeatureGuard allow={canMenu("pushbroom_mounts")}>
        <PushbroomCalibrationPage />
      </FeatureGuard>
    }
  />
  <Route path="/pushbroom/calibration" element={<Navigate to="/pushbroom/mounts" replace />} />
  ```
  `/pushbroom/mounts` 本任务先挂旧的标定页，Task 10 换成新页面。
- 在 `FeatureGuard` 后面加：
  ```tsx
  /** 旧的推扫记录地址：带上 ?job= 跳到工作台，工作台按任务找到架次。 */
  function LegacyPushbroomJobs() {
    const { search } = useLocation();
    return <Navigate to={`/pushbroom${search}`} replace />;
  }
  ```
- 旧的 `PushbroomJobs.tsx` 里有 `<Link to="/pushbroom/calibration">`，文件在本任务删除，不用改。

`rbac.tsx`：开关读到之前用上次的结果，推扫菜单不闪：

```tsx
const FEATURES_KEY = "mosaic_features";

function cachedFeatures(): Features {
  try {
    const raw = JSON.parse(localStorage.getItem(FEATURES_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}
```

放在 `const Ctx = …` 之前；`useState<Features>({})` 改为 `useState<Features>(cachedFeatures)`；`reload` 里 `setFeatures({ pushbroom: … })` 改为：

```tsx
    const next = { pushbroom: pushbroom.status === "fulfilled" && pushbroom.value.enabled };
    localStorage.setItem(FEATURES_KEY, JSON.stringify(next));
    setFeatures(next);
```

- [ ] **Step 9: 样式**

`theme.css` 末尾追加（`.pb-preview-large*` 旧样式留着不动，Task 10 结束后若没有引用再删）：

```css
/* 推扫工作台 */
.pb-wb {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pb-wb-body {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.pb-wb-side {
  position: sticky;
  top: 16px;
  padding: 12px;
  border-radius: 12px;
  background: #fff;
  max-height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}

.pb-wb-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.pb-wb-mid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
  align-items: stretch;
}

.pb-wb-empty {
  min-height: 360px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pb-flight-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  flex: 1;
}

.pb-flight-tools {
  display: flex;
  gap: 8px;
}

.pb-flight-scroll {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: auto;
  min-height: 0;
  flex: 1;
  padding-right: 2px;
}

.pb-flight-card {
  display: flex;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--mosaic-border, #e5e7eb);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.pb-flight-card:hover,
.pb-flight-card:focus-visible {
  border-color: #91caff;
  outline: none;
}

.pb-flight-card.is-active {
  border-color: #1677ff;
  background: #f0f7ff;
}

.pb-flight-card.is-bad {
  cursor: not-allowed;
  opacity: 0.6;
}

.pb-flight-thumb {
  flex: none;
  width: 56px;
  height: 56px;
  border-radius: 6px;
  object-fit: cover;
  background: #f1f5f9;
}

.pb-flight-thumb--empty {
  display: inline-block;
}

.pb-flight-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.pb-flight-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
}

.pb-flight-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
}

.pb-flight-manual {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px solid var(--mosaic-border, #e5e7eb);
  padding-top: 8px;
}

.pb-history {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow: auto;
}

.pb-history-item {
  padding: 8px 10px;
  border: 1px solid var(--mosaic-border, #e5e7eb);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pb-history-item:hover,
.pb-history-item:focus-visible {
  border-color: #91caff;
  outline: none;
}

.pb-history-item.is-active {
  border-color: #1677ff;
  background: #f0f7ff;
}

.pb-history-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pb-history-row > :last-child {
  margin-left: auto;
}

.pb-history-meta {
  font-size: 12px;
}

.pb-preview-box {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 360px;
  max-height: 60vh;
  border-radius: 8px;
  background: #0f172a;
  overflow: hidden;
}

.pb-preview-box img {
  max-width: 100%;
  max-height: 60vh;
  object-fit: contain;
  cursor: zoom-in;
}

.pb-state {
  padding: 48px 0;
}

.pb-timings {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pb-logs {
  max-height: 360px;
  overflow: auto;
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.pb-row-focus > td {
  background: #fffbe6 !important;
}

@media (max-width: 1200px) {
  .pb-wb-mid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .pb-wb-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .pb-wb-side {
    position: static;
    max-height: none;
  }
}
```

- [ ] **Step 10: 删旧页面并检查**

```bash
cd mosaic/server/frontend
git rm src/pages/pushbroom/PushbroomNew.tsx src/pages/pushbroom/PushbroomJobs.tsx
npx tsc --noEmit -p tsconfig.json && npm run build
```

Expected: 无错误。`rg "PushbroomNew|PushbroomJobs|pushbroom_new|pushbroom_jobs" src` 只剩 `roles.ts` 以外没有命中。

- [ ] **Step 11: 重建前端并在浏览器走一遍工作台**

```bash
cd mosaic/server
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml build frontend
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d frontend
```

用浏览器打开 `http://localhost:9080/pushbroom`，管理员登录，逐项确认并截图：

1. 侧栏「推扫拼图」分组只有「推扫拼图」「安装配置」两项；刷新页面时分组不闪。
2. 左栏架次按飞行时间倒序，0410 在最后变灰并写明原因；0414、0419、0429 显示「0414 安装」标签。
3. 点 0429_134344：右侧顶部信息、安装配置、历次任务都对；默认选中最近一次，主区域显示结果预览；点图打开全屏，能缩放拖动。
4. 点历次任务里另一条，预览和底部页签跟着换；地址栏 `flight`、`job` 跟着变；刷新后停在同一处。
5. 底部四个页签都有内容；运行日志时间是北京时间（和「提交时间」对得上）。
6. 打开 `http://localhost:9080/pushbroom/jobs?job=<任意任务 id>`，跳到工作台并选中对应架次和任务。
7. 打开「选项」改任务名，再换一个架次，回来时选项已重置。

不要在这一步提交新任务；提交和进度在 Task 11 一起看。

- [ ] **Step 12: 提交**

```bash
git add mosaic/server/frontend/src/pages/pushbroom/workbench mosaic/server/frontend/src/roles.ts \
  mosaic/server/frontend/src/components/AppShell.tsx mosaic/server/frontend/src/rbac.tsx mosaic/server/frontend/src/theme.css
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图前端：以架次为中心的工作台，替换新建推扫和推扫记录。')"
```

`theme.css` 上若还有别人未提交的改动，先和用户确认再 `git add`；必要时用 `git add -p` 只加本任务追加的那一段。

---

### Task 10: 安装配置页

**Files:**
- Create: `mosaic/server/frontend/src/pages/pushbroom/mounts/MountTable.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/mounts/FlightMountTable.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/mounts/CalibrationPanel.tsx`
- Create: `mosaic/server/frontend/src/pages/pushbroom/mounts/PushbroomMounts.tsx`
- Delete: `mosaic/server/frontend/src/pages/pushbroom/PushbroomCalibration.tsx`
- Modify: `mosaic/server/frontend/src/components/AppShell.tsx`、`api.ts`

**Interfaces:**
- Consumes: Task 8 的接口和工具；地址参数 `flight`（从工作台跳过来时高亮这一行）
- Produces：路由 `/pushbroom/mounts` 挂新页面

- [ ] **Step 1: 配置表**

新建 `mounts/MountTable.tsx`：

```tsx
import { useState } from "react";
import { Button, Card, Form, Input, Modal, Space, Table, Typography, message } from "antd";
import { api, type PushbroomMount } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import { meters, pixels } from "../labels";

type EditValues = { name: string; effective_from: string };

export default function MountTable({
  mounts,
  builtin,
  loading,
  onChanged,
}: {
  mounts: PushbroomMount[];
  builtin: { along_offset_m: number; boresight_px: number } | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<PushbroomMount | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<EditValues>();
  const rows = [...mounts].sort((a, b) => b.effective_from.localeCompare(a.effective_from));

  async function save() {
    if (!editing) return;
    const values = await form.validateFields();
    setSaving(true);
    try {
      await api.pushbroomUpdateMount(editing.id, { name: values.name.trim(), effective_from: values.effective_from });
      message.success("已保存");
      setEditing(null);
      onChanged();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  function remove(m: PushbroomMount) {
    Modal.confirm({
      title: `删除「${m.name}」？`,
      content: "已提交的任务不受影响；之后这些架次会按日期改用别的配置。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "返回",
      onOk: async () => {
        try {
          await api.pushbroomDeleteMount(m.id);
          message.success("已删除");
          onChanged();
        } catch (err) {
          message.error((err as Error).message || String(err));
        }
      },
    });
  }

  return (
    <Card className="mosaic-panel" title="安装配置">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        每个架次按飞行日期，用生效日期不晚于它的最近一组。比所有配置都早的架次用内置值
        {builtin ? `（沿航向 ${meters(builtin.along_offset_m)}，狭缝中心 ${pixels(builtin.boresight_px)}）` : ""}。
        拆装或更换相机后，在下面用新架次标定，再保存成一组新配置。
      </Typography.Paragraph>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: "还没有安装配置，所有架次都用内置值" }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "生效日期", dataIndex: "effective_from", width: 120 },
          { title: "沿航向偏移", width: 150, render: (_: unknown, m: PushbroomMount) => meters(m.along_offset_m, m.along_se_m) },
          { title: "狭缝中心偏置", width: 160, render: (_: unknown, m: PushbroomMount) => pixels(m.boresight_px, m.boresight_se_px) },
          { title: "标定架次", render: (_: unknown, m: PushbroomMount) => m.flights.join("、") || "—" },
          {
            title: "保存",
            width: 200,
            render: (_: unknown, m: PushbroomMount) => `${m.created_by || "管理员"} · ${formatJobDateTime(m.created_at).slice(0, 16)}`,
          },
          { title: "对到的架次", dataIndex: "matched", width: 100 },
          {
            title: "操作",
            width: 170,
            render: (_: unknown, m: PushbroomMount) => (
              <Space size={0}>
                <Button
                  type="link"
                  onClick={() => {
                    setEditing(m);
                    form.setFieldsValue({ name: m.name, effective_from: m.effective_from });
                  }}
                >
                  改名称和日期
                </Button>
                <Button type="link" danger onClick={() => remove(m)}>
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        open={Boolean(editing)}
        title="改名称和生效日期"
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={save}
        onCancel={() => setEditing(null)}
        forceRender
      >
        <Typography.Paragraph type="secondary">两个参数值来自标定，不能改。</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="effective_from" label="生效日期" rules={[{ required: true, message: "请选择日期" }]}>
            <Input type="date" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
```

- [ ] **Step 2: 架次对应表**

新建 `mounts/FlightMountTable.tsx`：

```tsx
import { Button, Card, Table, Typography, message } from "antd";
import { api, type PushbroomFlightItem } from "../../../api";
import MountTag from "../MountTag";
import { MOUNT_SOURCE, flightStartText, meters, pixels } from "../labels";

export default function FlightMountTable({
  flights,
  focus,
  loading,
  onChanged,
}: {
  flights: PushbroomFlightItem[];
  focus: string | null;
  loading: boolean;
  onChanged: () => void;
}) {
  async function undo(item: PushbroomFlightItem) {
    try {
      await api.pushbroomSetFlightMount(item.name, null);
      message.success(`${item.name} 已改回按日期`);
      onChanged();
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  return (
    <Card className="mosaic-panel" title="各架次用哪组">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        手动指定在工作台架次顶部的「改选」里设置，这里可以撤销。
      </Typography.Paragraph>
      <Table
        rowKey="input_dir"
        size="small"
        loading={loading}
        dataSource={flights}
        pagination={false}
        rowClassName={(item) => (item.name === focus ? "pb-row-focus" : "")}
        columns={[
          { title: "架次", dataIndex: "name" },
          { title: "飞行时间", width: 160, render: (_: unknown, f: PushbroomFlightItem) => flightStartText(f.flight) },
          { title: "安装配置", render: (_: unknown, f: PushbroomFlightItem) => <MountTag mount={f.mount} /> },
          {
            title: "数值",
            width: 220,
            render: (_: unknown, f: PushbroomFlightItem) => `${meters(f.mount.along_offset_m)} · ${pixels(f.mount.boresight_px)}`,
          },
          { title: "来源", width: 90, render: (_: unknown, f: PushbroomFlightItem) => MOUNT_SOURCE[f.mount.source] },
          {
            title: "操作",
            width: 110,
            render: (_: unknown, f: PushbroomFlightItem) =>
              f.mount.source === "manual" ? (
                <Button type="link" onClick={() => undo(f)}>
                  撤销手动
                </Button>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}
```

- [ ] **Step 3: 新建配置（后台标定）**

新建 `mounts/CalibrationPanel.tsx`：

```tsx
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Spin, Tag, Typography, message } from "antd";
import { api, type PushbroomCalibrationTask, type PushbroomFlightItem } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import { flightDay, formatDuration, meters, pixels } from "../labels";

const POLL_MS = 3000;
const RUN_STATUS: Record<PushbroomCalibrationTask["status"], { text: string; color: string }> = {
  running: { text: "标定中", color: "processing" },
  done: { text: "已完成", color: "success" },
  failed: { text: "失败", color: "error" },
};

type SaveValues = { name: string; effective_from: string };

function defaults(run: PushbroomCalibrationTask, flights: PushbroomFlightItem[]): SaveValues {
  const days = run.flights
    .map((name) => flights.find((f) => f.name === name))
    .map((f) => (f ? flightDay(f.flight) : ""))
    .filter(Boolean)
    .sort();
  const day = days[0] || "";
  return { effective_from: day, name: day ? `${day.slice(5, 7)}${day.slice(8, 10)} 安装` : "" };
}

function ResultList({ run }: { run: PushbroomCalibrationTask }) {
  return (
    <Flex vertical gap={6}>
      {(run.result?.flights || []).map((row) => (
        <Flex key={row.flight || row.error} justify="space-between" gap={12}>
          <Typography.Text>{row.flight || "—"}</Typography.Text>
          <Typography.Text type="secondary">
            {row.error
              ? row.error
              : row.calibration?.usable
                ? `${meters(row.calibration.along_suggested_m, row.calibration.along_se_m)} · ${pixels(row.calibration.boresight_suggested_px, row.calibration.boresight_se_px)}`
                : row.calibration?.note || "不可用"}
          </Typography.Text>
        </Flex>
      ))}
    </Flex>
  );
}

function RunResult({
  run,
  flights,
  onSaved,
}: {
  run: PushbroomCalibrationTask;
  flights: PushbroomFlightItem[];
  onSaved: () => void;
}) {
  const [form] = Form.useForm<SaveValues>();
  const [saving, setSaving] = useState(false);
  const combined = run.result?.combined;

  if (run.status === "running") {
    const since = run.created_at ? (Date.now() - Date.parse(run.created_at)) / 1000 : null;
    return (
      <Flex align="center" gap={12}>
        <Spin />
        <Typography.Text>
          正在标定 {run.flights.join("、")}，已用 {formatDuration(since)}。离开本页不影响计算。
        </Typography.Text>
      </Flex>
    );
  }
  if (run.status === "failed") {
    return <Alert type="error" showIcon message="标定失败" description={run.error || "没有记录原因"} />;
  }
  if (!combined?.usable) {
    return (
      <Flex vertical gap={12}>
        <Alert type="warning" showIcon message="这组架次不能一起标定" description={combined?.note || "换有往返航带、且安装状态相同的架次"} />
        <ResultList run={run} />
      </Flex>
    );
  }

  async function save(values: SaveValues) {
    if (!run.run_token) return;
    setSaving(true);
    try {
      await api.pushbroomCreateMount({
        run_id: run.id,
        run_token: run.run_token,
        name: values.name.trim(),
        effective_from: values.effective_from,
      });
      message.success("已保存。之后提交的任务按生效日期使用这组配置。");
      onSaved();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Flex vertical gap={12}>
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="沿航向偏移">{meters(combined.along_offset_m, combined.along_se_m)}</Descriptions.Item>
        <Descriptions.Item label="狭缝中心偏置">{pixels(combined.boresight_px, combined.boresight_se_px)}</Descriptions.Item>
      </Descriptions>
      <ResultList run={run} />
      {run.run_token ? (
        <Form<SaveValues> key={run.id} form={form} layout="inline" initialValues={defaults(run, flights)} onFinish={save}>
          <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="effective_from" label="生效日期" rules={[{ required: true, message: "请选择日期" }]}>
            <Input type="date" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存为安装配置
            </Button>
          </Form.Item>
        </Form>
      ) : (
        <Alert type="warning" showIcon message="标定结果已超过 30 分钟，请重新标定后再保存" />
      )}
    </Flex>
  );
}

export default function CalibrationPanel({
  flights,
  workerOffline,
  onSaved,
}: {
  flights: PushbroomFlightItem[];
  workerOffline: boolean;
  onSaved: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [runs, setRuns] = useState<PushbroomCalibrationTask[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const busy = runs.some((r) => r.status === "running");
  const current = runs.find((r) => r.id === currentId) ?? runs[0] ?? null;

  const loadRuns = useCallback(async () => {
    const data = await api.pushbroomCalibrationRuns();
    setRuns(data.items);
  }, []);

  useEffect(() => {
    loadRuns().catch((err) => message.error((err as Error).message || String(err)));
  }, [loadRuns]);

  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => loadRuns().catch(() => undefined), POLL_MS);
    return () => window.clearInterval(t);
  }, [busy, loadRuns]);

  async function start() {
    setStarting(true);
    try {
      const run = await api.pushbroomStartCalibration(picked);
      setRuns((cur) => [run, ...cur]);
      setCurrentId(run.id);
      setPicked([]);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setStarting(false);
    }
  }

  const reason = workerOffline ? "推扫计算服务未在线" : busy ? "已有一次标定在进行" : picked.length === 0 ? "先勾选架次" : "";

  return (
    <Card className="mosaic-panel" title="新建安装配置">
      <Flex vertical gap={16}>
        <Typography.Text type="secondary">
          勾选同一次安装、有往返航带的架次。拆装前后的架次不要放在一起。每个架次大约 1 分钟，离开本页不影响计算。
        </Typography.Text>
        <Checkbox.Group
          value={picked}
          onChange={(v) => setPicked(v as string[])}
          options={flights.map((f) => ({ value: f.input_dir, label: `${f.name}（${flightDay(f.flight) || "日期未知"}）` }))}
        />
        <Flex align="center" gap={12}>
          <Button type="primary" disabled={Boolean(reason)} loading={starting} onClick={start}>
            {picked.length ? `标定这 ${picked.length} 个架次` : "开始标定"}
          </Button>
          {reason && <Typography.Text type="secondary">{reason}</Typography.Text>}
        </Flex>
        {runs.length > 0 && (
          <Flex vertical gap={6}>
            <Typography.Text strong>最近的标定</Typography.Text>
            {runs.map((r) => (
              <Flex
                key={r.id}
                gap={8}
                align="center"
                className={`pb-history-item${current?.id === r.id ? " is-active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => setCurrentId(r.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") setCurrentId(r.id);
                }}
              >
                <Tag color={RUN_STATUS[r.status].color}>{RUN_STATUS[r.status].text}</Tag>
                <Typography.Text>{formatJobDateTime(r.created_at).slice(0, 16)}</Typography.Text>
                <Typography.Text type="secondary" ellipsis>
                  {r.flights.join("、")}
                </Typography.Text>
              </Flex>
            ))}
          </Flex>
        )}
        {current && <RunResult run={current} flights={flights} onSaved={onSaved} />}
      </Flex>
    </Card>
  );
}
```

`.pb-history-item` 默认是纵向排列；这里用 `Flex` 渲染，`Flex` 的行内样式会覆盖为横向，不用另加样式。

- [ ] **Step 4: 页面**

新建 `mounts/PushbroomMounts.tsx`：

```tsx
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Flex, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomMount, type PushbroomStatus } from "../../../api";
import CalibrationPanel from "./CalibrationPanel";
import FlightMountTable from "./FlightMountTable";
import MountTable from "./MountTable";

export default function PushbroomMountsPage() {
  const [params] = useSearchParams();
  const focus = params.get("flight");
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [mounts, setMounts] = useState<PushbroomMount[]>([]);
  const [builtin, setBuiltin] = useState<{ along_offset_m: number; boresight_px: number } | null>(null);
  const [flights, setFlights] = useState<PushbroomFlightItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, m, f] = await Promise.all([api.pushbroomStatus(), api.pushbroomMounts(), api.pushbroomFlights()]);
      setStatus(st);
      setMounts(m.items);
      setBuiltin(m.builtin);
      setFlights(f.items.filter((item) => item.ok));
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  return (
    <Flex vertical gap={16}>
      {status?.worker_online === false && <Alert type="warning" showIcon message="推扫计算服务未在线，现在不能标定" />}
      <MountTable mounts={mounts} builtin={builtin} loading={loading} onChanged={load} />
      <FlightMountTable flights={flights} focus={focus} loading={loading} onChanged={load} />
      <CalibrationPanel flights={flights} workerOffline={status?.worker_online === false} onSaved={load} />
    </Flex>
  );
}
```

- [ ] **Step 5: 换路由、删旧页、删旧接口**

`AppShell.tsx`：`import PushbroomCalibrationPage …` 换成 `import PushbroomMountsPage from "../pages/pushbroom/mounts/PushbroomMounts";`，`/pushbroom/mounts` 路由里 `<PushbroomCalibrationPage />` 换成 `<PushbroomMountsPage />`。

`api.ts`：删掉 `PushbroomCalibrationState` 类型、`pushbroomCalibration`、`pushbroomCalibrate`、`pushbroomApplyCalibration` 三个方法。`PushbroomCalibrationRun` 类型保留（`PushbroomCalibrationTask.result` 用到）。

```bash
cd mosaic/server/frontend
git rm src/pages/pushbroom/PushbroomCalibration.tsx
rg "pb-preview-large|PushbroomCalibrationState|pushbroomCalibrate\b|pushbroomApplyCalibration|/pushbroom/calibration\"" src
npx tsc --noEmit -p tsconfig.json && npm run build
```

Expected：`rg` 只命中 `AppShell.tsx` 里那条 `/pushbroom/calibration` 跳转和 `theme.css` 里的 `.pb-preview-large*`；`pb-preview-large` 没有组件引用了，把 `theme.css` 里 `.pb-preview-large-wrap`、`.pb-preview-large` 两段删掉（同样注意别人未提交的改动）。打包无错误。

- [ ] **Step 6: 提交**

```bash
git add mosaic/server/frontend/src/pages/pushbroom/mounts mosaic/server/frontend/src/components/AppShell.tsx \
  mosaic/server/frontend/src/api.ts mosaic/server/frontend/src/theme.css
tree=$(git write-tree) && git update-ref HEAD "$(git commit-tree "$tree" -p HEAD -m '推扫拼图前端：安装配置页，标定改为后台运行，替换相机标定页。')"
```

---

### Task 11: 完整走查

**Files:** 无代码改动；发现问题回到对应任务修。

- [ ] **Step 1: 全部测试**

```bash
cd mosaic/server/backend && PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_pushbroom_*.py tests/test_eta_and_menus.py
cd ../frontend && npx tsc --noEmit -p tsconfig.json && npm run build
```

Expected：全部 PASS，打包无错误。

- [ ] **Step 2: 重建前端，重启后端**

```bash
cd mosaic/server
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml build frontend
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d frontend
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml restart backend
```

- [ ] **Step 3: 浏览器走查（管理员）**

每一步截图，出问题记下来回到对应任务修：

1. 工作台选 0429_134344，「选项」里填任务名「走查」，提交。页面不跳转，历次任务最上面出现新任务并选中，主区域显示「排队中」或进度。
2. 进度推进时七步进度条、百分比、左栏卡片进度同步变化；运行日志自动滚到最新，时间是北京时间。
3. 完成后主区域出现结果预览；「打包下载」能下载 zip。
4. 安装配置页：配置表有「0414 安装」，对到的架次数等于 0414、0419、0429 三个日期的可用架次数；0410 若可用则显示「内置」。
5. 新建配置：勾选 0419_132008 一个架次开始标定，离开页面再回来，标定仍在进行或已出结果。结果可用时，生效日期默认 2026-04-19，名称默认「0419 安装」。**不要保存**（不改动现有生产配置）；确认按钮可用即可。
6. 把「0414 安装」的生效日期改成 2026-04-13 再改回 2026-04-14，架次对应表跟着刷新，名称数值不变。
7. 工作台 0419_132008 顶部「改选」，指定「0414 安装」并保存，卡片标签显示「0414 安装 · 手动」；到安装配置页撤销，回到「按日期」。
8. 用操作员账号登录：侧栏只有「推扫拼图」；看不到「改选」；直接打开 `/pushbroom/mounts` 被送回首页。
9. 停掉推扫 worker（`docker compose … stop pushbroom-worker`），工作台顶部出现黄色提示，提交仍然可以排队；安装配置页「开始标定」不可用并写明原因。走查完 `docker compose … start pushbroom-worker`，并取消这条排队任务。

走查里新建的任务（编号 #18 及以后）可以在确认后删除；不要动 #9–#17。

- [ ] **Step 4: 结束**

全部通过后，用 superpowers:finishing-a-development-branch 收尾，向用户汇报走查截图和遗留问题。
