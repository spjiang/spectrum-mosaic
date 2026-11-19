-- 推扫拼图任务。与框幅任务 job_runs 分开，计算由独立的推扫 worker 完成

CREATE SEQUENCE IF NOT EXISTS pushbroom_jobs_seq;

CREATE TABLE IF NOT EXISTS pushbroom_jobs (
  id UUID PRIMARY KEY,
  seq BIGINT NOT NULL DEFAULT nextval('pushbroom_jobs_seq'),
  title TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES users(id),
  status VARCHAR(32) NOT NULL,
  run_attempt INTEGER NOT NULL DEFAULT 1,
  input_dir TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  flight JSONB NOT NULL DEFAULT '{}'::jsonb,
  current_stage VARCHAR(32),
  stage_progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  global_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error_summary TEXT,
  stage_timings JSONB NOT NULL DEFAULT '{}'::jsonb,
  products JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

ALTER SEQUENCE pushbroom_jobs_seq OWNED BY pushbroom_jobs.seq;

CREATE UNIQUE INDEX IF NOT EXISTS pushbroom_jobs_seq_uidx ON pushbroom_jobs (seq);

CREATE INDEX IF NOT EXISTS pushbroom_jobs_owner_created ON pushbroom_jobs (created_by, created_at DESC);

CREATE INDEX IF NOT EXISTS pushbroom_jobs_status ON pushbroom_jobs (status);

CREATE TABLE IF NOT EXISTS pushbroom_job_logs (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES pushbroom_jobs(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  level VARCHAR(16) NOT NULL DEFAULT 'info',
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS pushbroom_job_logs_job_id_id ON pushbroom_job_logs (job_id, id)
