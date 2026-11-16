# 内部发版台（release）设计规格

> 状态：第一期已落地（实现见 `release/` 与本目录 plan）  
> 日期：2026-11-14  
> 范围：`release/`（发版台）+ `mosaic/server/worker`（离线验签钩子）+ 客户交付 compose  
> 性质：第一期内部工具；只部署在我方环境，不进入客户包

---

## 1. 目标与非目标

### 1.1 本期目标

1. 在仓库根目录新增 `release/`，提供内部可视化发版台：版本构建登记、客户档案、许可签发、交付包下载。
2. 发版台使用 FastAPI + 简单前端 + PostgreSQL，整套 Docker 部署；PG 数据卷与产物卷可迁移到其它服务器继续管理。
3. 客户发布镜像在构建时将 `ms_mosaic` 核心模块编译为原生扩展（`.so`），镜像内不保留对应算法 `.py`；客户 compose 不挂载源码目录。
4. 使用离线 Ed25519 签名许可：私钥仅存发版台，公钥打入客户 worker 镜像；worker 启动/接任务时验签并校验到期日。
5. 一版镜像供多客户复用；按客户签发不同到期日的许可文件，再打交付 zip。

### 1.2 非目标（本期不做）

- 客户自助申请页、自助下载门户。
- 在线吊销、必须回连发版台才能计算。
- 机器指纹绑定。
- 每个客户单独加密一份镜像。
- 将 `release/` 部署到客户环境。
- 对 backend / frontend 做 Cython（算法不在这两侧；本期按普通发布镜像处理）。
- 把算法改造成远程计算（算法仍在客户机本地跑，仅以编译 + 许可降低源码被直接拷走的风险）。

---

## 2. 已确认决策

| 项 | 选择 |
| --- | --- |
| 第一期形态 | 内部发版台（管理员使用） |
| 许可联网 | 完全离线 |
| 机器绑定 | 不做；只校验签名与到期日 |
| 数据库 | PostgreSQL（Docker 卷），不用 SQLite |
| 保护手段 | Cython 编译 worker 算法 + 离线签名许可 + 客户 compose 去掉源码挂载 |
| 镜像策略 | 每版本构建一次，多客户共用；许可按客户签发 |
| 目录名 | 产品代码在 `mosaic/`；发版台在 `release/` |

---

## 3. 目录与职责

```text
spectrum-mosaic/
  mosaic/server/          拼图产品（frontend / backend / worker / db）
  release/
    server/               发版台：FastAPI、前端、compose（含 postgres）
    build/                从 mosaic 取源码 → 编译 → 打客户镜像的脚本与 Dockerfile
    keys/                 签发私钥（gitignore；仅卷挂载，不进 git / 不进客户包）
    artifacts/            构建产物与交付 zip（gitignore 或专用卷）
  docs/
```

| 位置 | 负责 | 不负责 |
| --- | --- | --- |
| `release/server` | 客户、版本、签发、交付包、管理员登录、审计 | 不跑拼图任务 |
| `release/build` | 编译 `ms_mosaic`、构建客户镜像、导出/登记 digest | 不改算法业务逻辑 |
| `mosaic/server` | 拼图能力；发布版 worker 内嵌公钥并验许可 | 不保管签发私钥 |
| PostgreSQL 卷 | 客户 / 版本 / 许可 / 交付 / 审计元数据 | 可不存镜像 tar 本体 |
| `artifacts` 卷 | 镜像 tar、构建日志、交付 zip | 迁机时与 PG 卷一并搬迁 |

客户包中不得包含 `release/` 目录树或签发私钥。

---

## 4. 页面与主流程

四个页面：

| 页面 | 功能 |
| --- | --- |
| 版本 | 登记版本号；触发/记录构建；展示镜像名、digest、状态、时间、日志入口 |
| 客户 | 单位名、联系人、备注；状态：试用 / 正式 / 停用 |
| 许可 | 选客户 + 版本 → 填到期日 → 私钥签发 → 下载 `.lic`；查看历史；标记停用（仅发版台记账；离线侧仍以到期日为准） |
| 交付包 | 选客户 + 已成功版本 + 有效许可 → 生成 zip 并下载 |

主流程：

```text
构建版本 → 新建/选用客户 → 签发许可 → 生成交付包 → 人工交付给客户部署
```

---

## 5. 数据模型（PostgreSQL）

| 表 | 主要字段 |
| --- | --- |
| `admins` | 登录账号与密码哈希（第一期可引导创建一个管理员） |
| `customers` | 名称、联系人、状态、备注、时间戳 |
| `releases` | 版本号、镜像名、digest、构建状态（pending/running/success/failed）、日志路径、产物路径、时间戳 |
| `licenses` | 客户 ID、版本 ID、license_id、到期日、签发时间、文件校验和、状态（active/disabled） |
| `deliveries` | 客户 ID、版本 ID、许可 ID、包路径、校验和、创建时间 |
| `audit_logs` | 操作者、动作、对象、详情、时间 |

库内对大文件只存路径与 checksum；文件实体在 `artifacts` 卷。

---

## 6. 许可文件格式

- 载体：JSON 载荷 + Ed25519 签名（独立字段或附加签名块，实现时固定一种并写进客户 README）。
- 载荷字段：`license_id`、`customer`（或 customer_id 快照名）、`product`（固定如 `spectrum-mosaic`）、`release_version`、`expires_at`、`issued_at`。
- 私钥：`release/keys/ed25519_private.pem`（或等价安全存储）；公钥编译/拷贝进客户 worker 镜像。
- 校验规则（客户 worker）：签名有效 ∧ 当前 UTC 时间 ≤ `expires_at` ∧ product 匹配。第一期不校验机器指纹、不回连发版台。

---

## 7. 构建与客户交付

### 7.1 构建

1. 多阶段 Dockerfile，源码上下文来自 `mosaic/server`（或明确拷贝列表，避免把 `release/keys` 打进上下文）。
2. 构建阶段：Cython 编译 `ms_mosaic` 核心模块 → `.so`，删除对应 `.py`；保留极薄入口模块（无算法）。
3. 运行阶段：仅拷贝 `.so`、入口、依赖、公钥；非 root；客户 compose 只挂数据与许可。
4. 成功后：`docker save` 或打私有仓库 tag；digest/checksum 写入 `releases`；日志进 `artifacts`。

### 7.2 客户交付 zip

```text
customer-pack/
  docker-compose.yml      # 无 ./worker、./backend/app 源码挂载
  .env.example
  licenses/<id>.lic
  README.md               # load 镜像、挂许可、启动
  images/                 # 可选：docker save 的 tar；或 README 写仓库拉取方式
```

### 7.3 客户侧验签

- worker 启动或接到任务时读取挂载的许可文件。
- 失败或过期：拒绝计算，日志明确「许可无效或已过期」。
- 开发用明文镜像可不强制许可（通过构建参数区分 `dev` / `customer`），避免拖慢日常开发；客户发布镜像必须强制验签。

---

## 8. 错误处理

| 情况 | 行为 |
| --- | --- |
| 构建失败 | `releases.status=failed`，页面可查看日志 |
| 私钥缺失 | 拒绝签发，提示先初始化密钥 |
| 版本未成功构建 | 禁止生成交付包 |
| 许可过期/签名无效 | worker 不执行任务，写出明确错误 |
| 许可在发版台标记停用 | 仅影响新交付与台账；已发出的离线文件仍以到期日为准（本期不在线吊销） |

---

## 9. 安全边界（诚实预期）

- 目标：显著提高「直接拷贝 `.py` 源码即可盗用」的成本。
- 非目标：在具备逆向能力的对手面前做到算法不可还原。客户机器上必须能跑计算，内存与 `.so` 仍可能被分析。
- 私钥永不进入客户镜像、客户包或 git。

---

## 10. 验收标准（第一期）

1. `release/server` 可用 compose 启动；管理员可登录；PG 数据落在命名卷。
2. 能登记客户、触发/记录一次客户镜像构建；成功记录含 digest；失败可看日志。
3. 能签发带到期日的 `.lic`；用错误签名或过期日验证 worker 拒绝运行。
4. 客户镜像内不存在 `ms_mosaic` 核心算法对应的 `.py`（抽检）；客户 compose 无源码挂载。
5. 能生成交付 zip，按 README 在干净环境 load 后，持有效许可可跑通最小任务路径（或约定的 smoke 检查）。
6. `release/keys` 与 `artifacts` 不进入 git；迁机说明写明需同时迁移 PG 卷与 artifacts 卷。

---

## 11. 实现顺序（供后续 plan 拆解）

1. 脚手架：`release/server`（API + 前端 + postgres compose）与密钥初始化命令。
2. 数据模型与四个页面的 CRUD / 签发 / 下载 API。
3. `release/build` 客户镜像 Dockerfile 与构建编排；产物登记。
4. `mosaic` worker 离线验签钩子 + 客户 compose 模板。
5. 交付包打包与端到端验收。
