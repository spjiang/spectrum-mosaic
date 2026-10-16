/** 参数填写帮助：说明见后端 description；此处补充示例与改动效果。 */
export type ParamHelp = {
  summary?: string;
  examples: string[];
  tips?: string[];
  up?: string;
  down?: string;
  off?: string;
};

export const PARAM_HELP: Record<string, ParamHelp> = {
  input_dir: {
    examples: ["/data/input/MAX_20251017/MAX_20251017_001", "/data/input/其它测区名/架次目录"],
    tips: ["容器约定：server/data/input/<测区名>/<架次目录> 映射为 /data/input/...", "只读，不要把输出写进此目录"],
  },
  output_dir: {
    examples: ["/data/output/runs/replay_max_20251017_rgb", "/data/output/runs/job_01"],
    tips: ["容器约定：server/data/output/runs/<任务名>", "其下会生成 拼图结果/、附件/、cache/、log/"],
  },
  cache_dir: {
    examples: ["留空 → 默认 {output_dir}/cache/features", "/data/output/runs/某任务/cache/features"],
    tips: ["续跑/复现时可指向已有特征缓存，显著加快空三"],
    off: "留空用输出目录下 cache/features；清空该目录则空三重做",
  },
  log_dir: {
    examples: ["留空 → 默认 {output_dir}/log"],
  },
  process_dir: {
    examples: ["留空 → 默认 {output_dir}/附件"],
  },
  products_dir_name: {
    examples: ["拼图结果"],
    tips: ["交付约定用这个名字，改了下载路径会变"],
  },
  run_mode: {
    examples: ["全流程", "跑到指定阶段", "逐步确认"],
    tips: ["交付常用全流程", "跑到指定阶段时需同时填停止阶段"],
  },
  start_stage: {
    examples: ["S0_io", "S2_at", "S5_ortho"],
    tips: ["从中间阶段续跑时，需已有对应检查点/产物"],
    up: "越往后跳过越多；从 S5 起应同时填 reuse_dsm",
    down: "从更早阶段开始等于重算",
  },
  stop_after_stage: {
    examples: ["S4_dsm", "S5_ortho", "留空（full 模式）"],
    off: "全流程时忽略；填了会在该节点等待继续",
  },
  preset: {
    examples: ["通用", "RGB 快速预览"],
    tips: ["RGB 快速预览会强制只跑 Color、并走全流程"],
  },
  benchmark_dir: {
    examples: [
      "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/拼图结果",
      "留空 → 不做调试比对",
    ],
    tips: ["调试用：写比对报告.txt，不改格网、覆盖、颜色", "目录内需有 DSM.tif 与 Orthomosaic_pix_surf_group0.tif"],
    off: "留空不写比对报告",
  },
  match_reference_color: {
    examples: ["false（合格主路径）"],
    tips: ["调试套色，等于抄商业颜色；调算法时不要开"],
    up: "打开后 RGB 低频套到参考图，不是算法自己的颜色",
    off: "关闭才是合格主路径",
  },
  max_index: {
    examples: ["留空（全量）", "120（调试：只扫编号≤120）"],
    up: "覆盖更多曝光，更慢更完整",
    down: "只做局部调试，拼图会缺角",
    off: "留空扫描全部编号",
  },
  max_frames: {
    examples: ["留空（全量）", "12（调试预览）"],
    up: "用更多曝光，覆盖更完整",
    down: "预览更快，DSM/正射更容易空洞",
    off: "交付必须留空",
  },
  min_agl_m: {
    examples: ["5.0"],
    up: "更多起飞/降落帧被扔，测区边缘可能缺影像",
    down: "可能把地面滑行/标定帧吃进空三",
  },
  max_tilt_deg: {
    examples: ["60.0"],
    up: "侧视也进网，接缝和遮挡更难",
    down: "可用片减少，航带边缘空洞增加",
  },
  drop_white_panel: {
    examples: ["true（推荐）"],
    up: "打开：白板不当场景",
    off: "关闭会把高亮板当地面，匹配和外点暴增",
  },
  require_pos: {
    examples: ["true（推荐）"],
    tips: ["关闭后仍然会丢无 POS 帧：没有 GNSS 初值无法建测区"],
    off: "无 POS 帧仍会丢弃",
  },
  primary_band: {
    examples: ["Color"],
    tips: ["主路径固定 Color，对应 LiMapper 组 0"],
  },
  workers_at: {
    examples: ["4", "8", "12"],
    tips: ["上限会按任务内存预算和 CPU 自动下调"],
    up: "提特征/匹配更快，内存约 450MB/进程",
    down: "更慢但更稳；OOM 时降到 4～8",
  },
  memory_gb: {
    examples: ["0", "36"],
    tips: ["0=按容器空闲内存自动封顶；滑块上限=Docker Memory Limit；配高了不会多分内存"],
    up: "只放宽并行上限，不会真多分到内存",
    off: "0 表示按引擎可用内存自动封顶",
  },
  cpus: {
    examples: ["0", "8"],
    tips: ["0=按 Docker CPU 自动封顶；滑块上限=Desktop CPU limit；配高了不会多给核"],
    up: "配高不会超过 Docker CPU limit",
    off: "0 表示按引擎 CPU 封顶",
  },
  sigma_xy_m: {
    examples: ["3.0"],
    up: "更信影像、平面可能漂，适合 POS 很差",
    down: "更钉死 GPS。本批 XMP 为 3 m，再小会把真误差拧进网形",
  },
  sigma_z_m: {
    examples: ["5.0"],
    up: "高程更靠立体，碗状可能减轻",
    down: "高程被 GPS 拉住。本批 GPS Z 约 6 m RMSE，不要小于 3",
  },
  sigma_attitude_deg: {
    examples: ["3.0"],
    up: "姿态更自由，弱纹理区可能抖",
    down: "更信云台；正下视默认 3° 够用",
  },
  outlier_threshold_px: {
    examples: ["6.0"],
    up: "留更多错匹配，接缝容易花",
    down: "点更干净但可能剔光导致平差失败。6 px 是报告口径",
  },
  calibrate_intrinsics: {
    examples: ["true（推荐）"],
    up: "分阶段释放畸变，修厂家未给的 k/p/b",
    off: "内参钉死初值；本批 XMP 畸变全 0，关闭会出现边缘错位",
  },
  dsm_gsd: {
    examples: ["留空 → 按航高/焦距自动估计", "0.1（手写）"],
    tips: ["正射 GSD 约为 DSM 的一半，不要为对齐某份商业图而手写"],
    up: "格网更粗、更快、文件更小",
    down: "更细，内存和时间近似平方增长",
    off: "留空按航高/焦距估计，这是合格主路径",
  },
  reuse_dsm: {
    examples: [
      "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/runs/full_surface_rgb/拼图结果/DSM.tif",
      "留空 → 重新做密集匹配",
    ],
    tips: ["指定后跳过 S3 密集匹配，只重做正射"],
    off: "留空则重算立体",
  },
  n_layers: {
    examples: ["48"],
    up: "高程采样更细、更慢；64 以上收益很小",
    down: "台阶感、树冠被削；少于 24 明显糊",
  },
  z_margin_m: {
    examples: ["留空 → 按稀疏点起伏估计", "12.0"],
    up: "能跟上陡坎/高树，更慢、弱纹理更易配错",
    down: "快，但真表面在窗外会整片错高",
    off: "留空按稀疏点 p90−p10 估计（夹在 8～48 m）",
  },
  workers_dense: {
    examples: ["留空 → CPU-1", "12"],
    up: "立体更快，每进程约 2GB",
    down: "更稳、防 OOM",
    off: "留空由 CPU 与内存自动封顶",
  },
  spike_tolerance_m: {
    examples: ["2.5"],
    up: "尖刺留下，屋顶/树尖更碎",
    down: "陡坡和树冠被削平；不要小于 1.5",
  },
  max_fill_gap_m: {
    examples: ["40.0"],
    up: "航带边缘更满，可能把测区外涂进来",
    down: "边缘黑洞更多",
  },
  bands: {
    examples: ['["Color"]', '["Color","450nm","550nm","650nm","720nm","750nm","800nm","850nm"]'],
    tips: ["仅 RGB 预览填 Color；全波段可留默认 8 项"],
    up: "多加波段时间近似线性增加",
    down: "只留 Color 则没有多光谱产品",
  },
  color_correction: {
    examples: ["off_for_ms", "on", "off"],
    up: "on / off_for_ms：RGB 航带亮度更匀；多光谱始终不加增益",
    off: "off：RGB 也不做增益，接缝处可能一道亮一道暗",
  },
  seamline_enabled: {
    examples: ["true"],
    up: "写出附件/seamlines.geojson 便于质检",
    off: "关闭仍内部图割镶嵌，只是不写附件",
  },
  workers_ortho: {
    examples: ["留空 → 默认并行", "8"],
    up: "正射分块更快",
    down: "防 OOM；多光谱还会再拆成波段并发",
    off: "留空按内存自动封顶",
  },
  write_pdf_report: {
    examples: ["true"],
    off: "关闭则无法下载质量报告.pdf",
  },
  write_json_report: {
    examples: ["true"],
    off: "关闭不影响 GeoTIFF，只少机器可读报告",
  },
};
