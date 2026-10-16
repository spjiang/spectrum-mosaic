-- 本测区商业覆盖 > 足迹，收边 20 m 会明显偏小。全流程方案改为 0。
UPDATE param_definitions
   SET default_value = '0.0'::jsonb,
       description = $d$交付覆盖从相片足迹外缘往里收的米数。默认 0：本测区商业有效区比足迹还大，再收 20 m 会小到商业的 84%。外沿油彩用贴边平面纠正，不要靠收边去贴商业面积。$d$
 WHERE key = 'edge_trim_m';

INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, 'edge_trim_m', '0.0'::jsonb
  FROM param_profiles p
 WHERE p.name = '测区示例 · MAX_20251017 · 全流程'
ON CONFLICT (profile_id, param_key) DO UPDATE SET value = EXCLUDED.value;
