# 系统配置 · 注意事项（多条 · 数据库）

日期：2026-11-14  
状态：已批准并实现

## 目标

「系统配置 → 注意事项」支持多条记录：新建 / 编辑 / 查看 / 逻辑删除、分页；存 PostgreSQL。

## 数据模型 `notes`

| 字段 | 说明 |
|------|------|
| id | 递增主键 |
| title | 标题 |
| body | Markdown 正文 |
| created_at / updated_at | 时间戳 |
| deleted_at | 逻辑删除 |

## API

- `GET /api/settings/notes` — 未删除列表（按 updated_at 降序）
- `POST /api/settings/notes` — `{ title, body }`
- `PATCH /api/settings/notes/{id}` — 更新
- `DELETE /api/settings/notes/{id}` — 软删

## 迁移

- `004_notes.sql`
- 启动时若表空且存在 `artifacts/notes/注意事项.md`，导入为一条「迁移自文件 · 注意事项」
- 之后以库为准，不再读写该文件作为主存储

## UI

列表：ID、标题、摘要、更新时间、查看/编辑/删除；弹窗编辑含 Markdown 预览。
