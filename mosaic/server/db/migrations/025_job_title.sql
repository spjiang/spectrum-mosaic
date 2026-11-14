-- 处理任务标题。空标题在应用层补成「#任务号 YYYY-MM-DD HH:MM」，与列表创建时间同一钟面（UTC）。

ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';

UPDATE job_runs
SET title = '#' || seq::text || ' ' || to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI')
WHERE title = '';
