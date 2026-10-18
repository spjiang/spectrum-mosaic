-- 公开项：RGB 偏色默认关；边缘裁剪默认 0（不裁）。
-- 拼图分辨率倍数和优先拼接线影像供任务记录，不另起算法。

UPDATE param_definitions
   SET default_value = '"off"'::jsonb,
       advanced = false,
       label = 'RGB 偏色处理',
       description = $d$默认否（off）。打开（on）后只对 RGB 做全图曝光增益，多光谱始终不加。off_for_ms 与 on 相同，只开 RGB。$d$
 WHERE key = 'color_correction';

UPDATE param_profile_values
   SET value = '"off"'::jsonb
 WHERE param_key = 'color_correction'
   AND value = '"off_for_ms"'::jsonb;

UPDATE param_definitions
   SET label = '边缘裁剪（米）',
       description = $d$默认否：0 表示不裁。填大于 0 的米数才从相片足迹外缘往里收。$d$
 WHERE key = 'edge_trim_m';

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'ortho_scale',
  'S0_io',
  'int',
  '1'::jsonb,
  $d$拼图分辨率相对原始分辨率的倍数。1 为原始，2 和 4 更粗、更快。DSM 仍是拼图分辨率的 2 倍。锁定交付格网时此项不生效。$d$,
  false,
  21,
  false,
  '拼图分辨率倍数'
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'seam_prefer_ids',
  'S5_ortho',
  'string',
  '""'::jsonb,
  $d$优先进入拼接线的影像号，逗号分隔。空则完全自动。只改变数据项里这些影像的优先级，视角数、立面层数和图割轮数不变。$d$,
  false,
  31,
  false,
  '优先拼接线影像'
)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS native_gsd_m DOUBLE PRECISION;
ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS ortho_gsd_m DOUBLE PRECISION;
