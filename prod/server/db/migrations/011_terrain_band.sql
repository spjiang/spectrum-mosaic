-- DSM 地形带余量：随测区地物/航带变化，禁止只写死在代码里。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES
(
  'terrain_margin_lo_m',
  'S4_dsm',
  'float',
  '30.0'::jsonb,
  $d$DSM 地形带下余量（米）：空三点 p1 再往下留的 AT/洼地余量。MAX_20251017 示例 30。林区洼地深或 GPS 高程噪声大时加大。$d$,
  true,
  30,
  false,
  '地形带下余量（米）'
),
(
  'terrain_margin_hi_m',
  'S4_dsm',
  'float',
  '50.0'::jsonb,
  $d$DSM 地形带上余量（米）：空三点 p99 再往上留的树冠/建筑余量。MAX_20251017 示例 50。树高或楼高超过该值会削顶，须加大。$d$,
  true,
  40,
  false,
  '地形带上余量（米）'
),
(
  'terrain_min_half_span_m',
  'S4_dsm',
  'float',
  '80.0'::jsonb,
  $d$无空三参考时，DSM 中值±半宽的下限（米）。MAX_20251017 起伏 161 m 故用 80。更平坦测区可减小，否则浅坑可能留下。$d$,
  true,
  50,
  false,
  '无空三半宽下限（米）'
)
ON CONFLICT (key) DO NOTHING;

UPDATE param_definitions
   SET description = $d$空洞填充最大跨度（米）。MAX_20251017 示例 40（本测区缺口 p99）。航带更宽、单视区更大时加大，否则边缘正射白边。$d$,
       label = '空洞填充跨度（米）'
 WHERE key = 'max_fill_gap_m';

UPDATE param_definitions
   SET description = $d$相对中值超过此高差视为尖刺剔除。本测区示例 2.5；更碎的屋顶可略加大，陡坡不要小于 1.5。$d$,
       label = '尖刺剔除阈值（米）'
 WHERE key = 'spike_tolerance_m';
