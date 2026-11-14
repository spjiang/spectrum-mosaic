-- 任务 CPU 预算（核）。0 = 按 Docker 引擎可见 CPU 自动封顶并行度。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'cpus',
  'S0_io',
  'int',
  '0'::jsonb,
  $d$本任务可用 CPU 核数。0 表示按 Docker 引擎 CPU（Desktop → Settings → Resources → CPU）自动封顶。配得再高也不会超过引擎可见核数。$d$,
  false,
  26,
  false,
  '任务 CPU（核）'
)
ON CONFLICT (key) DO NOTHING;
