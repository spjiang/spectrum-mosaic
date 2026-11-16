# 客户操作手册（发版台入口 + 交付附带）

日期：2026-11-14  
状态：已批准（方案 A）

## 目标

在「发版管理」下提供「操作手册」页：预览 Markdown、下载 `.md` / `.pdf`，打交付包时自动放入 `docs/`。

## 内容范围

一份给客户的完整手册：

1. 部署交付：Docker、compose、许可挂载、启动、续期
2. 业务使用：登录、新建处理、处理记录、GPU/资源注意、常见问题
3. 不含发版台内部操作与算法源码说明

## 文件与 API

- 源文件：`release/docs/customer-manual.md`、`release/docs/customer-manual.pdf`
- 下载文件名：`操作手册.md` / `操作手册.pdf`
- `GET /api/manual` → `{ title, markdown, has_pdf }`
- `GET /api/manual/download?format=md|pdf` → 文件流（需登录）

## 前端

- 菜单：发版管理 → 操作手册 `/manual`
- 页内 Markdown 预览 + 两个下载按钮

## 交付包

`packager` 将上述两文件复制为：

- `docs/操作手册.md`
- `docs/操作手册.pdf`

简版 `README.md` 保留启动摘要，并指向 `docs/操作手册`。
