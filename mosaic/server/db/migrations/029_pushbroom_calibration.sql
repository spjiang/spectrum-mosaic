-- 推扫相机安装标定。生效记录是 created_at 最新的一行；没有行表示使用内置值。

CREATE TABLE IF NOT EXISTS pushbroom_calibrations (
  id UUID PRIMARY KEY,
  along_offset_m DOUBLE PRECISION NOT NULL,
  boresight_px DOUBLE PRECISION NOT NULL,
  along_se_m DOUBLE PRECISION NOT NULL,
  boresight_se_px DOUBLE PRECISION NOT NULL,
  flights JSONB NOT NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS pushbroom_calibrations_created ON pushbroom_calibrations (created_at DESC);
