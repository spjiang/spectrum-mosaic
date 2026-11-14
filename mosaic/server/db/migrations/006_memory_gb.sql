-- 任务内存预算（GB）。0 = 按容器当前可用内存自动封顶并行度。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'memory_gb',
  'S0_io',
  'float',
  '0'::jsonb,
  $d$本任务进程内存预算（GB）。0 表示按容器当前可用内存自动下调并行度。全量 668 张建议 36。须把 Docker 引擎内存调到不少于此值，Worker 才能真正用到。$d$,
  false,
  25,
  false,
  '任务内存（GB）'
)
ON CONFLICT (key) DO NOTHING;
