-- 空三参与波段。默认 8 个名字表示「数据里有的全部做」。显式改成子集时必须含 Color。

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'at_bands',
  'S2_at',
  'string[]',
  '["Color","450nm","550nm","650nm","720nm","750nm","800nm","850nm"]'::jsonb,
  $d$参与空三的波段。留空，或仍是这 8 个默认名时，数据里每个波段都提特征、匹配和平差。改成别的列表（例如 Color 与 800nm）时只解列出的波段；列表必须包含 Color。未列入的波段不做空三，正射时外方位从 Color 迁出。正射出哪些波段仍由「正射波段」决定。$d$,
  false,
  12,
  false,
  '空三波段'
)
ON CONFLICT (key) DO NOTHING;

UPDATE param_definitions
SET description = $d$外方位迁移的来源，固定 Color。空三解哪些波段由「空三波段」决定，不是只解这一路。$d$
WHERE key = 'primary_band';
