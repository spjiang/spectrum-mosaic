# 发版台：版本构建增加系统架构字段

日期：2026-11-14  
状态：已确认（待实现）

## 背景

客户交付为离线 Docker 镜像包。镜像必须与目标机 **CPU 架构** 一致；发行版版本（Ubuntu/CentOS）无需各打一套。

此前在 Apple Silicon 上构建得到的均为 `linux/arm64`，列表未展示架构，易误当成通用包交给 x86 服务器。

## 目标

1. 每条版本构建记录带有明确的系统架构字段。
2. 同一业务版本号可并存多架构构建（如同时有 amd64 与 arm64）。
3. 构建时按所选架构 `docker build/pull --platform`，产物 tag 带架构后缀，互不覆盖。
4. 交付包内 compose 使用该条构建的带架构镜像名。

## 非目标

- 不为各 Linux 发行版分别构建。
- 不做一次 buildx 多架构 manifest（离线 tar 不友好）。
- 不在本变更中自动探测客户机架构或远程构建农场。

## 决策摘要

| 项 | 选择 |
|----|------|
| 唯一性 | 未删除记录上 `(version, arch)` 唯一 |
| 默认架构 | 无；登记时必选 |
| 可选值 | `amd64`、`arm64`（UI 展示为 `linux/amd64`、`linux/arm64`） |
| 镜像 tag | `{repo}:{version}-{arch}`，例 `spectrum-mosaic-worker:0.1.0-rc1-amd64` |
| Docker platform | `linux/{arch}` |

## 数据模型

`releases` 表新增：

- `arch TEXT NOT NULL` — 取值仅 `amd64` | `arm64`

约束与迁移：

- 迁移 `006_release_arch.sql`：加列；既有行回填 `arm64`（与当前本机产物一致）；对 `deleted_at IS NULL` 建唯一索引 `(version, arch)`（或部分唯一，与现有 soft-delete 习惯一致）。
- API/模型：`Release.arch`；创建必填；编辑 **不允许改 arch**（避免产物与元数据错位；若需另一架构则新建一条版本记录）。

## API / Schema

- `ReleaseCreateIn`：`version` + `arch`（必填，枚举校验）。
- `ReleaseUpdateIn`：仅允许改 `version`（且仍受 `(version, arch)` 唯一约束）；不含 `arch`。
- `ReleaseOut`：增加 `arch`。
- 列表/详情均返回 `arch`。
- 创建时若同版本同架构且未删除已存在 → 400。

## 构建行为（`build_runner`）

1. 读取 `rel.arch`，`platform = f"linux/{rel.arch}"`。
2. worker / backend / frontend：`images.build(..., platform=platform)`，tag 为 `{repo}:{version}-{arch}`。
3. 依赖镜像 postgres / rabbitmq：`pull` 时指定同 platform，再 `save(named=True)`。
4. 构建日志首行写明 `platform=linux/...`。
5. `image_name` 存 worker 的完整 tag（含 `-{arch}`）。
6. 产物目录仍为 `artifacts/releases/{id}/`；五个 tar + manifest；manifest 含 `arch=` 与各 tag。

宿主机为 arm64 打 amd64 时依赖 Docker Desktop / buildx 的跨架构构建；失败则 `status=failed` 并写入可读错误（不静默落成本机架构）。

## 打包与 compose 模板

- `packager`：`{{IMAGE_NAME}}`、`{{VERSION}}`、新增 `{{ARCH}}`；backend/frontend 默认镜像名带 `-{arch}`。
- 交付 zip 文件名建议：`{customer}_{version}_{arch}_{license_id}.zip`。
- `customer-compose.yml` / `.env.example` / README：镜像名与说明体现架构；手册补一句「包内镜像架构须与宿主机一致」。

## 前端（版本构建页）

- 「登记版本」表单：版本号 + 架构下拉（必选，无默认占位如「请选择架构」）。
- 表格新增「架构」列（显示 `linux/amd64` 等）。
- 编辑弹窗不展示可改的架构字段（只读展示即可）。
- 构建确认文案带上架构。
- 交付包页选版本时，选项 label 含架构，避免同版本混淆。

## 错误处理

- 非法 `arch` → 400。
- `(version, arch)` 冲突 → 400，提示已存在。
- 跨架构 build 失败 → release `failed`，`error_message` 保留 Docker 错误摘要。

## 验收标准

1. 可登记 `0.1.0-rc1` + `amd64` 与 `0.1.0-rc1` + `arm64` 两条，互不冲突。
2. 列表可见架构；镜像名为 `…:0.1.0-rc1-amd64` 形式。
3. amd64 构建产物经 `docker image inspect` 为 `Architecture=amd64`（在支持跨架构的 Docker 环境下）。
4. 交付包 compose 中镜像 tag 与所选 release 架构一致；zip 名含架构。
5. 登记时不选架构无法提交。

## 范围外后续（可选）

- 发版台设置里的「常用架构」偏好（仍不作为强制默认）。
- 构建机队列按架构分流。
