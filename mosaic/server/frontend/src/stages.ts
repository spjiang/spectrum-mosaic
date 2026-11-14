/** 拼图流水线阶段（展示名 / 短说明） */
export const STAGES = [
  "S0_io",
  "S1_catalog",
  "S2_at",
  "S3_dense",
  "S4_dsm",
  "S5_ortho",
  "S6_report",
] as const;

export type StageId = (typeof STAGES)[number];

export const STAGE_META: Record<
  StageId,
  { label: string; short: string; hint: string }
> = {
  S0_io: { label: "输入输出", short: "IO", hint: "作用：确认从哪读、写到哪，并建好输出目录。路径不对就停，这一步不改影像" },
  S1_catalog: { label: "影像编目", short: "编目", hint: "作用：给航片建册，去掉白板、贴地和倾角过大的废片，只让可用照片进入后面的计算" },
  S2_at: { label: "空三解算", short: "空三", hint: "作用：求出每张照片的位置、朝向和稀疏地面点。后面的高程和正射都靠这个几何" },
  S3_dense: { label: "密集匹配", short: "密集", hint: "作用：给地面每个格子算一个高度，得到密的高程图，供 DSM 和正射使用" },
  S4_dsm: { label: "DSM 生成", short: "DSM", hint: "作用：去掉尖刺、补上小洞，把高程写成可交付的 DSM。正射要踩在这张地形上" },
  S5_ortho: { label: "正射镶嵌", short: "正射", hint: "作用：按 DSM 把照片压到地图上，拼成各波段正射图" },
  S6_report: { label: "质量报告", short: "报告", hint: "作用：汇总精度和覆盖，写出质量报告，用来判断这次成果能不能交付" },
};

export type StageTone = "wait" | "process" | "finish" | "error" | "pause";

export type JobStageLike = {
  id?: string | null;
  status?: string | null;
  current_stage?: string | null;
  completed_stage?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  stage_timings?: Record<string, number | string | null | { stage?: string; since?: string; beat?: string }> | null;
};

function stageIndex(id: string | null | undefined): number {
  if (!id) return -1;
  return STAGES.indexOf(id as StageId);
}

export function activeStageIndex(job: JobStageLike): number {
  const cur = stageIndex(job.current_stage);
  const done = stageIndex(job.completed_stage);
  if (job.status === "succeeded") return STAGES.length - 1;
  const furthest = Math.max(cur, done);
  if (furthest >= 0) {
    if (cur >= 0 && cur > done) return cur;
    if (job.status === "running" && done >= 0) return Math.min(done + 1, STAGES.length - 1);
    if (done >= 0) return done;
    return cur;
  }
  return job.status === "queued" || !job.status ? -1 : 0;
}

export function toneForStage(job: JobStageLike, stage: StageId): StageTone {
  const idx = STAGES.indexOf(stage);
  const done = stageIndex(job.completed_stage);
  const active = activeStageIndex(job);
  const status = job.status || "";

  if (status === "succeeded") return "finish";
  if (status === "queued") return "wait";
  if (done >= 0 && idx <= done) return "finish";
  if (idx < active) return "finish";
  if (idx === active) {
    if (status === "failed" || status === "cancelled") return "error";
    if (status === "paused" || status === "awaiting_continue") return "pause";
    return "process";
  }
  return "wait";
}

export function displayStage(job: JobStageLike): string | null {
  const cur = stageIndex(job.current_stage);
  const done = stageIndex(job.completed_stage);
  if (done > cur) return job.completed_stage || null;
  return job.current_stage || job.completed_stage || null;
}

export function stageLabel(id: string | null | undefined): string {
  if (!id) return "—";
  const meta = STAGE_META[id as StageId];
  return meta ? meta.label : id;
}
