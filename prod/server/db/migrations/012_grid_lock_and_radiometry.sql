-- 交付格网锁定与辐射归一化。两项都是对标商业成品时的实测结论，见 docs/算法出处.md。
INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES
(
  'grid_reference',
  'S4_dsm',
  'path',
  'null'::jsonb,
  $d$锁定交付格网：填参考成果目录（含 DSM.tif，可选正射）或单个 GeoTIFF，直接采用其 GSD / 原点 / 宽高。留空则按航高与焦距自动估计。自动估计的 GSD 实测比商业规格粗约 13%，原点还差半个像元，逐像元比对（灰度相关系数仅 0.20）量的是格网错位而不是影像质量；交付规格通常由任务书给定，这里就显式锁定。只锁格网，高程、覆盖、颜色仍由本次影像自己算。$d$,
  false,
  12,
  false,
  '锁定交付格网'
),
(
  'radiometric_normalize',
  'S5_ortho',
  'bool',
  'false'::jsonb,
  $d$按参考成果做全局仿射辐射归一化（需同时填 benchmark_dir）。自研正射忠实于源影像（G 均值 114.3，源 107.4，饱和 0.18%），商业成品是减掉暗电平后的档位（G 91.1，p99=196，饱和 0）。实测两者是斜率近 1、带负截距的仿射关系（R 0.9418x−13.66 / G 0.9612x−18.46 / B 0.9656x−18.17），p15–p95 局部斜率 0.94–1.09，既不是伽马也不是压高光。开启后分位残差由 18–23 DN 降到 0.9–1.7 DN、饱和率降到 0%，与商业一致。只改全局档位，不动纹理与局部对比。$d$,
  false,
  12,
  false,
  '辐射归一化到参考'
)
ON CONFLICT (key) DO NOTHING;

-- 「全流程」示例方案按商业实测规格交付：锁格网 + 辐射归一化，并留参考目录用于比对报告。
INSERT INTO param_profile_values (profile_id, param_key, value)
SELECT p.id, v.param_key, v.value
  FROM param_profiles p
  CROSS JOIN (VALUES
    ('grid_reference',        '"/data/input/MAX_20251017/拼图结果"'::jsonb),
    ('benchmark_dir',         '"/data/input/MAX_20251017/拼图结果"'::jsonb),
    ('radiometric_normalize', 'true'::jsonb)
  ) AS v(param_key, value)
 WHERE p.name = '测区示例 · MAX_20251017 · 全流程'
ON CONFLICT (profile_id, param_key) DO UPDATE SET value = EXCLUDED.value;

-- 低频替换会把参考的低频底搬进成果，只作调试用，交付方案里必须关闭。
UPDATE param_definitions
   SET description = $d$调试用：用参考正射的低频底替换自研低频（需 benchmark_dir）。会把参考的内容搬进成果，交付不得开启。要对齐商业亮度档位请用「辐射归一化到参考」，那是只改全局档位的仿射变换。$d$,
       label = '低频套色（调试）'
 WHERE key = 'match_reference_color';
