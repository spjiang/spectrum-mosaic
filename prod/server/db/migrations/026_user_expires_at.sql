-- 用户使用期限。空值表示不限期，已有账号保持不限期。

ALTER TABLE users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
