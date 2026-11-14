-- 交付收边：商业不交最外一圈单视/斜视。对标只抽米数写进模版，禁止套参考 alpha。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES
(
  'edge_trim_m',
  'S5_ortho',
  'float',
  '0.0'::jsonb,
  $d$交付覆盖从相片足迹外缘往里收的米数。默认 0：本测区商业有效区比足迹还大（35.73 ha vs 足迹约 34.4 ha），再收 20 m 会小到 29.96 ha（商业的 84%）。外沿油彩用贴边平面纠正，不要靠收边去贴商业面积。换测区若最外单视明显差，再加大。$d$,
  false,
  14,
  false,
  '边缘收边（米）'
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, 'edge_trim_m', '0.0'::jsonb
  FROM param_profiles p
 WHERE p.name LIKE '测区示例 · MAX_20251017%'
ON CONFLICT (profile_id, param_key) DO UPDATE SET value = EXCLUDED.value;
