-- 增量测区记录正式目录里的最新曝光号，并按间隔扫描收件区。

ALTER TABLE incremental_projects ADD COLUMN IF NOT EXISTS scan_interval_minutes INTEGER NOT NULL DEFAULT 5;
ALTER TABLE incremental_projects ADD COLUMN IF NOT EXISTS latest_shot_index INTEGER;
ALTER TABLE incremental_projects ADD COLUMN IF NOT EXISTS latest_filename VARCHAR(255);
ALTER TABLE incremental_projects ADD COLUMN IF NOT EXISTS latest_ingested_at TIMESTAMPTZ;
ALTER TABLE incremental_projects ADD COLUMN IF NOT EXISTS last_scan_at TIMESTAMPTZ;

UPDATE incremental_projects AS project
SET latest_shot_index = picked.shot_index,
    latest_filename = picked.filename,
    latest_ingested_at = picked.updated_at
FROM (
  SELECT DISTINCT ON (project_id) project_id, shot_index, filename, updated_at
  FROM incremental_files
  WHERE status = 'ingested'
  ORDER BY project_id, shot_index DESC, updated_at DESC
) AS picked
WHERE project.id = picked.project_id
  AND project.latest_shot_index IS NULL;
