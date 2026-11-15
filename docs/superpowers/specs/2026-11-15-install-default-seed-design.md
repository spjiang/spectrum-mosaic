# 安装初始化：默认处理方案 + 试用账号 + 目录权限

**日期：** 2026-11-15  
**状态：** 待用户审阅  
**范围：** mosaic 客户交付栈（`mosaic/server`）首次/空缺启动时的幂等种子数据  
**非目标：** 发版台（release console）；不自动创建 `zdrh`；不导入测区样例数据

## 背景

当前 `bootstrap()` 会：跑迁移、补参数字典、创建角色、创建 admin、补齐示例账号（executor / configurator / viewer）。

缺失：

- 两条交付默认「处理方案」（标准全波段、RGB 快速预览）
- 带使用期限的试用账号及其数据目录
- 明确把默认 RBAC 菜单映射落到库（空库依赖代码默认值，已有库可能缺菜单）

## 决策摘要（已确认）

| 项 | 选择 |
| --- | --- |
| 钩子 | mosaic `backend` 启动 `bootstrap()` 幂等补齐 |
| 试用账号 | `trial1` / `trial2`，密码均为 `trial@123456` |
| 角色 | `executor`（操作员） |
| 期限 | 安装/首次创建时起 **30 天**（`expires_at`，与现网「所选日期当天仍可用」语义一致） |
| 目录 | 自动创建 `/data/input/{user}`、`/data/output/runs/{user}` |
| 默认方案 | 「标准全波段」「RGB 快速预览」；`created_by = NULL`（全员可见） |
| 路径字段 | 方案内 `input_dir` / `output_dir` 不绑死某试用用户；新建任务时由用户填写本账号目录 |

## 架构

```
backend startup
  └─ bootstrap(db, settings)
       ├─ (existing) migrations / catalog / admin / demo role users
       ├─ _ensure_default_rbac(db)          # NEW
       ├─ _ensure_default_profiles(db)     # NEW
       └─ _ensure_trial_users(db, settings) # NEW → users + mkdir trial_dirs
```

权限模型不变：非 admin 用户测区/成果路径仍由 `assert_owned_dirs` + `trial_dirs(username)` 强制落在同名目录下。种子只负责账号 + 物理目录存在。

## 组件

### 1. 默认 RBAC

- 调用现有 `app.services.rbac.save_mapping(db, default_mapping())` **仅当** `system_settings.rbac_menus` 不存在时写入。
- 若已存在：不覆盖客户改过的菜单；可选仅对缺失角色键做补缺（实现时优先「不存在才写」，避免冲掉现网定制）。
- 默认菜单以 `DEFAULT_MENUS` 为准（含 execute / incremental / seam 等现行代码定义）。

### 2. 默认处理方案

幂等键：**按方案名**。名称已存在则跳过（不覆盖客户改过的参数）。

| 名称 | description | preset | 要点 values |
| --- | --- | --- | --- |
| 标准全波段 | 正式生产默认: 8 波段全图, 步进扫描（不包含窄带扫描） | `null` | `bands` = 8 波段；`run_mode=full`；关闭调试套色；其余对齐现网 #1 核心参数，缺省参数走 `param_definitions` |
| RGB 快速预览 | 仅 Color 正射, 快速查看几何与拼接 | `rgb_preview` | 应用 `RGB_PREVIEW_VALUES`（Color / full） |

实现注意：

- `created_by=NULL`，列表对所有登录角色可见。
- `input_dir` / `output_dir`：写入与现网一致的**占位路径**（如 `/data/input`、`/data/output/runs`）或 `null`；**不得**写成 `/data/input/trial1/...`，避免其它账号选方案时路径校验失败。创建方案时跳过 `assert_owned_dirs`（种子用 admin 会话语义 / 内部 API 直接写表）。
- `version` 初始为 `1`（不必伪造现网的 48）。
- 参数集合以现网 profile #1/#2 为模板子集 + 目录字段策略；未写入的键在跑任务时合并字典默认值。

参数模板落盘位置建议：`mosaic/server/backend/app/seed/default_profiles.py`（常量 dict），便于单测，避免巨型 SQL。

### 3. 试用账号

对 `trial1`、`trial2`：

1. 用户不存在则创建：`password_hash=hash_password("trial@123456")`，`is_active=True`，角色 `executor`。
2. `expires_at`：若不存在则设为「创建时刻起第 30 个日历日结束」（与 UI「日期当天仍可用、次日不能登录」对齐：存当天 23:59:59.999 UTC+约定，或与现网 `zdrh` 一样存期末时刻；实现时复用 Users 页同一换算）。
3. 已存在用户：**不改密码、不改角色、不刷新期限**（避免重启发版把试用期无限续期）。
4. 无论用户是否新建，都 `Path(trial_dirs).mkdir(parents=True, exist_ok=True)`；目录创建失败只打日志，不阻断整个 bootstrap（数据卷未挂载时仍应能起 API）。

示例账号 `executor` / `configurator` / `viewer` 行为不变（不限期）。

### 4. 配置（可选）

无需新增必填环境变量。若后续要关掉试用种子，可加 `BOOTSTRAP_TRIAL_USERS=0`（本版可不做，YAGNI）。

## 数据流

1. 客户 `docker compose up` → backend lifespan → `bootstrap`。
2. 缺 RBAC 行 → 写入默认菜单。
3. 缺两方案名 → 插入 `param_profiles` + `param_profile_values`。
4. 缺 trial 用户 → 插入用户 + 角色；mkdir 目录。
5. 前端登录 `trial1` → 可见两方案；新建处理只能选本账号目录。

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 方案名已存在 | 跳过 |
| 试用用户已存在 | 跳过字段更新；仍 ensure 目录 |
| mkdir 失败 | warning 日志；bootstrap 继续 |
| 参数字典尚未就绪 | 先跑现有 catalog ensure，再插方案 |

## 测试

- 单元：构建默认方案 values；试用 `expires_at` 约等于 now+30d；`trial_dirs` mkdir 路径。
- 单元/集成：空库连续两次 bootstrap，方案数与 trial 用户数不翻倍、期限不被刷新。
- 路径：`assert_owned_dirs("trial1", ...)` 拒绝对他人目录的访问（已有测试可扩一条 trial1）。

## 文档

- 客户手册或试用说明可增补默认账号表（`trial1`/`trial2`）；本设计实现时可只改代码，手册另开任务。

## 验收

- 全新客户库启动后：处理方案列表至少含上述两条；用户管理可见 `trial1`/`trial2`（操作员、约 30 天期限）。
- 容器内存在对应 input/output 根目录（卷已挂载时）。
- 二次启动数据不重复、试用期限不重置。
