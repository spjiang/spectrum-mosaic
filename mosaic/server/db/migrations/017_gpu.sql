-- 任务 GPU：auto 自动选空闲显存最多的卡；off 只用 CPU；数字为指定卡号。
-- 没有 GPU 运行库、没有卡或该阶段 GPU 实现未通过验收时，一律退回 CPU。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'gpu',
  'S0_io',
  'string',
  '"auto"'::jsonb,
  $d$本任务使用的 GPU。auto 自动选空闲显存最多的卡；off 只用 CPU；0、1… 指定卡号。没有显卡或该阶段 GPU 实现未通过验收时自动使用 CPU，结果与 CPU 一致。$d$,
  false,
  27,
  false,
  '任务 GPU'
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'gpu_memory_gb',
  'S0_io',
  'float',
  '0'::jsonb,
  $d$本任务可用显存上限（GB）。0 表示用所选显卡当前空闲显存的 85%。配得再高也不会超过空闲显存。$d$,
  false,
  28,
  false,
  '任务显存（GB）'
)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS gpu TEXT;
ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS gpu_memory_gb DOUBLE PRECISION;

UPDATE job_runs
   SET gpu = params_snapshot->>'gpu'
 WHERE gpu IS NULL
   AND params_snapshot ? 'gpu'
   AND jsonb_typeof(params_snapshot->'gpu') IN ('number', 'string');

UPDATE job_runs
   SET gpu_memory_gb = (params_snapshot->>'gpu_memory_gb')::double precision
 WHERE gpu_memory_gb IS NULL
   AND params_snapshot ? 'gpu_memory_gb'
   AND jsonb_typeof(params_snapshot->'gpu_memory_gb') = 'number';
