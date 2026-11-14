-- 潜在较差影像判定线：点数低于 max(下限, 均值 × 比例) 才列入。
-- 默认 80 与 0.20，与原先写死的口径相同。

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'poor_keypoint_min',
  'S6_report',
  'int',
  '80'::jsonb,
  $d$较差影像的绝对下限（特征点数）。判定线取它和「均值 × 比例」里更大的那个。默认 80。$d$,
  false,
  30,
  false,
  '较差影像下限（点）'
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'poor_keypoint_fraction',
  'S6_report',
  'float',
  '0.2'::jsonb,
  $d$较差影像相对均值的比例。判定线取「下限」和「均值 × 本比例」里更大的那个。默认 0.20。$d$,
  false,
  40,
  false,
  '较差影像均值比例'
)
ON CONFLICT (key) DO NOTHING;
