-- 推扫安装配置：架次按飞行日期选生效日期不晚于它的最近一组，手动指定优先。
CREATE TABLE IF NOT EXISTS pushbroom_mount_profiles (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  effective_from DATE NOT NULL UNIQUE,
  along_offset_m DOUBLE PRECISION NOT NULL,
  along_se_m DOUBLE PRECISION NOT NULL,
  boresight_px DOUBLE PRECISION NOT NULL,
  boresight_se_px DOUBLE PRECISION NOT NULL,
  flights JSONB NOT NULL DEFAULT '[]',
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 单个架次手动指定的配置。配置删除时一并删除。
CREATE TABLE IF NOT EXISTS pushbroom_flight_profiles (
  flight_name TEXT PRIMARY KEY,
  profile_id UUID NOT NULL REFERENCES pushbroom_mount_profiles(id) ON DELETE CASCADE,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 后台标定记录。页面轮询状态，离开页面结果也在。
CREATE TABLE IF NOT EXISTS pushbroom_calibration_runs (
  id UUID PRIMARY KEY,
  status VARCHAR(16) NOT NULL,
  input_dirs JSONB NOT NULL,
  result JSONB,
  error TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pushbroom_calibration_runs_created ON pushbroom_calibration_runs (created_at DESC);