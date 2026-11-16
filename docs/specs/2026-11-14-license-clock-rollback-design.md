# 许可时钟回拨检测

日期：2026-11-14  
状态：已批准（方案 A）

## 目标

客户离线环境下，防止通过回拨系统时间绕过许可到期；异常时直接拒绝运行。

## 规则

- 仅当 `MOSAIC_REQUIRE_LICENSE=1` 时启用。
- 验签与生效/到期校验通过后，再做回拨检测。
- 持久化文件默认：`/data/.license/clock.json`（可用 `LICENSE_CLOCK_PATH` 覆盖）。
- 字段：`license_id`、`last_seen_at`（UTC ISO）。
- 若文件存在且 `license_id` 相同，且 `now < last_seen_at − 1h` → 拒绝，文案「系统时间异常，疑似时钟回拨」。
- 通过后更新 `last_seen_at = now`。
- 文件不存在或 `license_id` 变更：不因回拨拒绝，写入当前许可的新记录。
- 容差：1 小时（防 NTP 微调误杀）。

## 范围

- `ms_mosaic.license_check` + 单测
- 客户手册 FAQ 补充
