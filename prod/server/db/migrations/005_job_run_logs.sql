CREATE TABLE IF NOT EXISTS job_run_logs (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  level VARCHAR(16) NOT NULL DEFAULT 'info',
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS job_run_logs_job_id_id ON job_run_logs (job_id, id);
