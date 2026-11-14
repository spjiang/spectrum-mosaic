-- 贴边平面纠正：商业边缘不交错误 DSM 上的真正射。对标只抽窗口/带宽写进模版。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES
(
  'flatten_edge_win_m',
  'S5_ortho',
  'float',
  '40.0'::jsonb,
  $d$贴边平面纠正的块平均窗口（米）。错误 DSM 上真正射比平面正射更差（Amhar 1998）。本测区西缘 DSM 比商业冠层低 26 m，原面重投是油彩波纹，改用 40 m 块平均面后纹理恢复。只供正射采样，不改交付 DSM。换测区树冠更碎可减小，更平可加大。$d$,
  true,
  16,
  false,
  '贴边纠正窗口（米）'
),
(
  'flatten_edge_band_m',
  'S5_ortho',
  'float',
  '80.0'::jsonb,
  $d$贴边平面纠正带宽（米）：距 nodata 这么远的格子才改用块平均面，内部真正射不动。本测区窗口实验 80 m 与商业边缘观感一致。$d$,
  true,
  17,
  false,
  '贴边纠正带宽（米）'
)
ON CONFLICT (key) DO NOTHING;

-- 本测区对标商业拼图结果后写入「全流程」方案。换测区另建方案，不要改代码。
INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, v.param_key, v.value
  FROM param_profiles p
  CROSS JOIN (VALUES
    ('grid_reference',          '"/data/input/MAX_20251017/拼图结果"'::jsonb),
    ('benchmark_dir',           '"/data/input/MAX_20251017/拼图结果"'::jsonb),
    ('radiometric_normalize',   'true'::jsonb),
    ('match_reference_color',   'false'::jsonb),
    ('edge_trim_m',             '0.0'::jsonb),
    ('flatten_edge_win_m',      '40.0'::jsonb),
    ('flatten_edge_band_m',     '80.0'::jsonb),
    ('terrain_margin_lo_m',     '30.0'::jsonb),
    ('terrain_margin_hi_m',     '50.0'::jsonb),
    ('terrain_min_half_span_m', '80.0'::jsonb),
    ('max_fill_gap_m',          '40.0'::jsonb),
    ('spike_tolerance_m',       '2.5'::jsonb),
    ('outlier_threshold_px',    '6.0'::jsonb),
    ('max_tilt_deg',            '60.0'::jsonb),
    ('color_correction',        '"off_for_ms"'::jsonb)
  ) AS v(param_key, value)
 WHERE p.name = '测区示例 · MAX_20251017 · 全流程'
ON CONFLICT (profile_id, param_key) DO UPDATE SET value = EXCLUDED.value;
