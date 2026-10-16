import { toneForStage, type JobStageLike, type StageId } from "./stages";

const LIVE = new Set(["queued", "running", "paused", "awaiting_continue"]);

function timingSeconds(job: JobStageLike, stage: string): number | null {
  const raw = job.stage_timings?.[stage];
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : null;
}

function openSlice(job: JobStageLike): { stage?: string; since?: string; beat?: string } | null {
  const raw = job.stage_timings?._open;
  if (!raw || typeof raw !== "object") return null;
  return raw;
}

/** 心跳超过这个时间还没刷新，就当成进程已经不在，不再往下加。 */
const BEAT_MS = 60_000;

function freshOpenSeconds(job: JobStageLike, stage: string, nowMs: number): number | null {
  const open = openSlice(job);
  if (!open || open.stage !== stage) return null;
  const since = Date.parse(String(open.since || ""));
  const beat = Date.parse(String(open.beat || ""));
  if (!Number.isFinite(since) || !Number.isFinite(beat)) return null;
  if (nowMs - beat > BEAT_MS) return null;
  return Math.max(0, (nowMs - since) / 1000);
}

/** 节点耗时等于日志里这段连续运行的起止。进程心跳断了就停，死机空档不加。 */
export function stageElapsedSeconds(job: JobStageLike, stage: StageId, nowMs = Date.now()): number | null {
  const tone = toneForStage(job, stage);
  const stored = timingSeconds(job, stage);
  if (tone === "wait") return null;
  const extra = tone === "process" ? freshOpenSeconds(job, stage, nowMs) : null;
  if (extra == null) return stored;
  return Math.max(0, (stored ?? 0) + extra);
}

export function formatStageSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 1) return "<1秒";
  const total = Math.round(seconds);
  if (total < 60) return `${total}秒`;
  const minutes = Math.floor(total / 60);
  const rem = total % 60;
  if (minutes < 60) return rem ? `${minutes}分${rem}秒` : `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  const restMin = minutes % 60;
  return restMin ? `${hours}小时${restMin}分` : `${hours}小时`;
}

export function formatJobDateTime(v?: string | null): string {
  if (!v) return "—";
  const s = String(v).replace("T", " ");
  return s.length >= 19 ? s.slice(0, 19) : s;
}

function activeElapsedSeconds(
  job: {
    status?: string | null;
    stage_timings?: Record<string, number | string | null | { stage?: string; since?: string; beat?: string }> | null;
  },
  nowMs: number,
): number | null {
  const timings = job.stage_timings;
  if (!timings) return null;
  let sum = 0;
  let any = false;
  for (const [key, raw] of Object.entries(timings)) {
    if (key === "_open" || raw == null || typeof raw === "object") continue;
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(n)) {
      sum += n;
      any = true;
    }
  }
  const open = timings._open;
  if (job.status === "running" && open && typeof open === "object" && open.stage) {
    const extra = freshOpenSeconds(job, open.stage, nowMs);
    if (extra != null) {
      sum += extra;
      any = true;
    }
  }
  return any ? sum : null;
}

export function jobElapsedHours(
  job: {
    started_at?: string | null;
    created_at?: string | null;
    finished_at?: string | null;
    status?: string | null;
    stage_timings?: Record<string, number | string | null | { stage?: string; since?: string; beat?: string }> | null;
  },
  nowMs = Date.now(),
): string {
  const active = activeElapsedSeconds(job, nowMs);
  if (active != null) return `${(active / 3600).toFixed(1)} 小时`;
  const startMs = Date.parse(String(job.started_at || job.created_at || ""));
  if (!Number.isFinite(startMs)) return "—";
  const live = LIVE.has(job.status || "");
  const endMs = live ? nowMs : Date.parse(String(job.finished_at || ""));
  if (!Number.isFinite(endMs)) return "—";
  const hours = Math.max(0, (endMs - startMs) / 3_600_000);
  return `${hours.toFixed(1)} 小时`;
}
