# 系统配置 · 全局配置

日期：2026-11-14  
状态：已实现

## 目标

发版台「系统配置 → 全局配置」维护键值；首项为回拨容差；打交付包时写入客户 compose；页面标注使用位置。

## 数据

表 `system_settings`：`key`、`value`、`label`、`description`、`used_by`、`updated_at`。

种子：`license_clock_tolerance_hours` 默认 `1`。

## 链路

1. 发版台保存 → PostgreSQL  
2. `build_delivery_zip` 读库 → 替换 `{{LICENSE_CLOCK_TOLERANCE_HOURS}}`  
3. 客户 worker 读 `LICENSE_CLOCK_TOLERANCE_HOURS` → `check_clock_rollback`
