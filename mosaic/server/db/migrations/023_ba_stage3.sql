-- 空三平差阶段 3 开关。默认打开，与原先写死的三阶段相同。
-- 关闭后停在阶段 2（f、cx、cy、k1、k2），不再估计 k3、p1、p2、b1、b2。

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'ba_stage3',
  'S2_at',
  'bool',
  'true'::jsonb,
  $d$默认打开：阶段 1、阶段 2 之后继续阶段 3，估计 k3、切向畸变和仿射项。关闭后停在阶段 2（焦距、主点、k1、k2），大约少 12 分钟。影像中心几乎不动，像幅四角最多大约 4 像素（约 15 厘米），正射接缝可能挪动大约 1 个像素。特征提取、匹配、阶段 1 和阶段 2 不变。$d$,
  false,
  21,
  false,
  '平差阶段3'
)
ON CONFLICT (key) DO NOTHING;
