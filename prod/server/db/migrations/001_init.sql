-- visual_server initial schema
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username VARCHAR(64) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS roles (
  id SERIAL PRIMARY KEY,
  name VARCHAR(32) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS param_definitions (
  key VARCHAR(128) PRIMARY KEY,
  stage_id VARCHAR(32) NOT NULL,
  value_type VARCHAR(32) NOT NULL,
  default_value JSONB,
  description TEXT NOT NULL,
  advanced BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS param_profiles (
  id SERIAL PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  version INT NOT NULL DEFAULT 1,
  preset VARCHAR(64),
  created_by INT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS param_profile_values (
  profile_id INT NOT NULL REFERENCES param_profiles(id) ON DELETE CASCADE,
  param_key VARCHAR(128) NOT NULL REFERENCES param_definitions(key),
  value JSONB NOT NULL,
  PRIMARY KEY (profile_id, param_key)
);

CREATE TABLE IF NOT EXISTS job_runs (
  id UUID PRIMARY KEY,
  created_by INT REFERENCES users(id),
  profile_id INT REFERENCES param_profiles(id),
  profile_version INT,
  status VARCHAR(32) NOT NULL,
  run_attempt INT NOT NULL DEFAULT 1,
  params_snapshot JSONB NOT NULL,
  input_dir TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  cache_dir TEXT,
  log_dir TEXT,
  process_dir TEXT,
  products_dir TEXT,
  report_pdf_path TEXT,
  current_stage VARCHAR(32),
  completed_stage VARCHAR(32),
  global_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
  stage_progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  eta_seconds INT,
  error_summary TEXT,
  stage_timings JSONB NOT NULL DEFAULT '{}'::jsonb,
  n_shots INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS job_runs_one_running
  ON job_runs ((status))
  WHERE status = 'running';

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  action VARCHAR(64) NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_settings (
  key VARCHAR(64) PRIMARY KEY,
  value JSONB NOT NULL
);

INSERT INTO roles (name) VALUES
  ('admin'), ('configurator'), ('executor'), ('viewer')
ON CONFLICT DO NOTHING;
