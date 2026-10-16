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
  S0_io: { label: "输入输出", short: "IO", hint: "白话：核对从哪读、写到哪，建好输出目录，还不改影像" },
  S1_catalog: { label: "影像编目", short: "编目", hint: "白话：航片点名入册，扔掉白板/贴地/大歪废片" },
  S2_at: { label: "空三解算", short: "空三", hint: "白话：算出每张照片在天上的位置朝向和稀疏地面点" },
  S3_dense: { label: "密集匹配", short: "密集", hint: "白话：多张重叠片对立体，给地面网格逐点估高" },
  S4_dsm: { label: "DSM 生成", short: "DSM", hint: "白话：去尖刺补小洞，写成可交付的 DSM 地形文件" },
  S5_ortho: { label: "正射镶嵌", short: "正射", hint: "白话：按地形把照片压平到地图上再拼成正射大图" },
  S6_report: { label: "质量报告", short: "报告", hint: "白话：汇总精度与覆盖，写出 PDF/JSON（可选比对报告）" },
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
