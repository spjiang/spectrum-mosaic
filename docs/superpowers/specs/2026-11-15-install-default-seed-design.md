# 安装初始化：init.sql（结构）+ init-data.sql（数据）

**日期：** 2026-11-15  
**状态：** 待用户审阅（已按「改成 SQL」修订）  
**范围：** mosaic 客户交付栈（`mosaic/server`）库结构与初始业务数据  
**非目标：** 发版台；不自动创建 `zdrh`；不导入测区样例航片

## 背景与约定

安装初始化拆成两个固定文件，放在源代码 SQL 目录：

| 文件 | 职责 |
| --- | --- |
| `mosaic/server/db/init.sql` | **数据库初始结构**（DDL：表、索引、约束等） |
| `mosaic/server/db/init-data.sql` | **全部初始业务数据**（DML：角色、用户、RBAC、默认处理方案及参数值等） |

原则：

- **结构归 `init.sql`，数据归 `init-data.sql`**，不要把种子 INSERT 写进结构文件，也不要用 Python 散落插入业务种子。
- 升级存量库仍可用 `mosaic/server/db/migrations/*.sql`（编号迁移）；**新装客户库**以 `init.sql` + `init-data.sql` 为准。
- 物理目录（`/data/input/...`）无法在 SQL 里创建 → bootstrap **仅**负责 `mkdir`，不写用户/方案数据。

现有 `migrations/001_init.sql` 与本设计中的 `init.sql` 对齐：实现时将「权威结构」落到 `db/init.sql`；空库 bootstrap 优先执行 `init.sql`（若暂未生成完整合并结构，可先由 `init.sql` 转发/等同当前结构基线 + 迁移链，但**对外文档与目录命名固定为 init.sql / init-data.sql**）。

## 决策摘要（已确认）

| 项 | 选择 |
| --- | --- |
| 结构文件 | `mosaic/server/db/init.sql` |
| 数据文件 | `mosaic/server/db/init-data.sql`（所有初始数据集中于此） |
| 试用账号 | `trial1` / `trial2`，密码均为 `trial@123456`，角色 `executor` |
| 期限 | 首次插入时 `expires_at = (CURRENT_DATE + 30) 日末`（幂等：已存在不刷新） |
| 目录 | bootstrap `mkdir`：`/data/input/{user}`、`/data/output/runs/{user}` |
| 默认方案 | 「标准全波段」「RGB 快速预览」；`created_by = NULL` |
| 方案路径 | `input_dir` / `output_dir` 不绑试用用户名 |

## 架构

```
backend startup → bootstrap(db, settings)
  ├─ 空库：执行 db/init.sql（结构）
  ├─ 存量升级：按需执行 migrations/*.sql（不变）
  ├─ 参数字典等既有 ensure（可逐步收拢；本版不强制迁入 init-data）
  ├─ 执行 db/init-data.sql（幂等种子）
  └─ _ensure_trial_dirs()：按 trial 用户名 mkdir（唯一残留的非 SQL 种子步骤）
```

## init-data.sql 内容清单

一律 `INSERT … ON CONFLICT DO NOTHING` 或「不存在则插入」写法，可重复执行。

1. **角色**（若结构文件未带角色行）：`admin` / `configurator` / `executor` / `viewer`
2. **系统与示例用户**（密码为预计算 bcrypt，与现网 `hash_password` 一致）  
   - `admin` / `admin123`（可用环境变量覆盖时：SQL 仍插默认；bootstrap 已存在则不改——与今行为一致）  
   - `executor` / `executor123`  
   - `configurator` / `configurator123`  
   - `viewer` / `viewer123`  
   - `trial1` / `trial2` / `trial@123456`，`expires_at` 见上  
3. **user_roles** 关联  
4. **RBAC**：`system_settings` 键 `rbac_menus`，值为现行 `DEFAULT_MENUS` JSON；**仅当该 key 不存在时插入**  
5. **处理方案**  
   - `标准全波段`：preset NULL；描述「正式生产默认: 8 波段全图, 步进扫描（不包含窄带扫描）」；核心 `param_profile_values` 对齐现网 #1（目录字段用占位，不写 trial 路径）  
   - `RGB 快速预览`：preset `rgb_preview`；描述「仅 Color 正射, 快速查看几何与拼接」；含 Color / full 等  

不在 `init-data.sql` 的：测区文件、任务记录、许可证。

## bootstrap 行为变更

- **删除/停用** Python 里用 ORM 创建 demo 用户、默认方案、RBAC 的逻辑（改为跑 `init-data.sql`）。
- 保留：迁移 ensure、admin 若不存在且仅当 SQL 未插入时的兜底（优先完全依赖 SQL）。
- 新增：解析 `db/init-data.sql` 路径（与 migrations 同级的 `db/` 目录）；执行后 `_ensure_trial_dirs`。

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 种子行已存在 | ON CONFLICT / WHERE NOT EXISTS 跳过 |
| trial 已存在 | 不更新密码与 `expires_at` |
| mkdir 失败 | 打 warning，不阻断启动 |
| 缺 `init-data.sql` | 启动失败并明确报错（交付包必须带此文件） |

## 测试

- 空库：只跑 `init.sql` + `init-data.sql` → 两方案、trial 用户、RBAC 齐全。  
- 再跑一遍 `init-data.sql` → 行数不增加、trial 期限不变。  
- 登录 `trial1` 密码可用；路径校验仍限制在自有目录。

## 验收

- 源码目录可见固定文件：`mosaic/server/db/init.sql`、`mosaic/server/db/init-data.sql`。  
- 新装启动后具备两条默认方案与 `trial1`/`trial2`；目录在卷挂载时已创建。  
- 二次启动不重复插入、不续期。

## 相对上一版的变更

- ~~Python `default_profiles.py` 插方案~~ → **`init-data.sql`**  
- ~~bootstrap 内 ORM 建试用用户~~ → **`init-data.sql`**；bootstrap 只 `mkdir`  
- 明确 **`init.sql` = 结构，`init-data.sql` = 全部初始数据**
