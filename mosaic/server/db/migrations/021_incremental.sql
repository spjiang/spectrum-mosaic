-- 增量拼图的测区、收件记录和轮次。不改现有任务表的含义。

CREATE TABLE IF NOT EXISTS incremental_projects (
  id UUID PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  input_dir TEXT NOT NULL UNIQUE,
  inbox_dir TEXT NOT NULL,
  output_root TEXT NOT NULL,
  auto_run BOOLEAN NOT NULL DEFAULT FALSE,
  quiet_minutes INTEGER NOT NULL DEFAULT 10,
  current_job_id UUID REFERENCES job_runs(id) ON DELETE SET NULL,
  current_output_dir TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS incremental_files (
  id BIGSERIAL PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES incremental_projects(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  shot_index INTEGER NOT NULL,
  band VARCHAR(32) NOT NULL,
  role VARCHAR(8) NOT NULL,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL,
  dataset_path TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (project_id, filename)
);

CREATE INDEX IF NOT EXISTS incremental_files_project ON incremental_files (project_id);

CREATE TABLE IF NOT EXISTS incremental_rounds (
  id BIGSERIAL PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES incremental_projects(id) ON DELETE CASCADE,
  job_id UUID REFERENCES job_runs(id) ON DELETE SET NULL,
  output_dir TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
