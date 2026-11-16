# 许可生效时间 `starts_at`

日期：2026-11-14  
状态：已批准（方案 A）

## 背景

签发许可原先只填到期时间；需要「未到生效日不可用」时，补上生效开始时间。

## 行为

- 字段名：`starts_at`（库表、签名 payload、API、UI）
- 签发/编辑弹窗：默认「此刻」，可手改；必须严格早于 `expires_at`
- 列表展示「生效」「到期」两列
- Worker / 发版台验签：`starts_at ≤ now ≤ expires_at`；未到生效日报「许可尚未生效」；过期仍报「许可无效或已过期」
- 改 `starts_at` / `expires_at` 时重新签名写 `.lic`（与现有改到期一致）
- 旧行：迁移回填 `starts_at = issued_at`；缺字段的旧 `.lic` 仅校验到期（兼容）

## 范围

- `licenses` 表 + `003_starts_at.sql`；bootstrap 依次跑 001–003
- release API / 前端许可页
- `license_crypto` 与 `ms_mosaic.license_check`
