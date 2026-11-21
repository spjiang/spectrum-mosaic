-- 相机类型与像元尺寸。框幅拼图算法不分相机，差异只在编目读法（命名、姿态写法、焦距初值、高程）。

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'camera_source',
  'S1_catalog',
  'enum',
  '"auto"'::jsonb,
  $d$按哪种相机读输入目录。auto 自动识别：文件名是 MAX_<编号>_<Color|波长nm>_<D|W|R> 的按 MAX 多光谱相机读；按序号命名（如 000000.tif）且 XMP 型号为 VEN-* 或厂商为 Wayho 的按 Wayho VEN 系列 RGB 相机读。max_ms、wayho_rgb 为手动指定。Wayho 相机 XMP 里俯仰、横滚为 0 表示正下视；没有绝对高程时相机高度取相对航高，DSM 为相对起飞点的高度。$d$,
  false,
  5,
  false,
  '相机类型'
)
ON CONFLICT (key) DO NOTHING;

INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
VALUES (
  'pixel_size_um',
  'S1_catalog',
  'float',
  'null'::jsonb,
  $d$传感器像元尺寸（微米），只用于非 MAX 相机：焦距初值 = 焦距(mm) × 1000 ÷ 像元尺寸。留空时按型号表（VEN-1220 为 1.85 µm），型号表里没有再读 EXIF 焦平面分辨率。焦距初值偏差大时空三可能不收敛，换新相机型号时请向厂家确认后填写。$d$,
  true,
  6,
  false,
  '像元尺寸（µm）'
)
ON CONFLICT (key) DO NOTHING;
