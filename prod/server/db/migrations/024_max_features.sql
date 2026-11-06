-- 每张影像的特征点上限。默认 8192，与 LiMapper「影像关键点数最大值」一致。

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'max_features',
  'S2_at',
  'int',
  '8192'::jsonb,
  $d$每张影像最多保留的 RootSIFT 点数。默认 8192，与 LiMapper 影像关键点数最大值一致。像幅先按 4×3 分块提取，再按响应强度截到这个数，避免纹理密的区域把名额占光。调高会增加匹配时间和描述子内存；调低会更快，弱纹理上的点先被裁掉。已有特征缓存若不是按这个上限提取的，会重新提点。复用空三检查点时不再提点。$d$,
  false,
  22,
  false,
  '特征点上限'
)
ON CONFLICT (key) DO NOTHING;
