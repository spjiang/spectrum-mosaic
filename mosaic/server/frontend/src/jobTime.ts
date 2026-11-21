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

const CST_MS = 8 * 60 * 60 * 1000;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** 没有时区的时间按 UTC 读。接口存的是 UTC，直接切片会少 8 小时。 */
function parseJobInstant(v: string): number | null {
  const raw = v.trim();
  if (!raw) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw);
  const iso = hasZone ? raw : `${raw.replace(" ", "T")}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

function formatShifted(ms: number, withSeconds: boolean): string {
  const cst = new Date(ms + CST_MS);
  const date = `${cst.getUTCFullYear()}-${pad2(cst.getUTCMonth() + 1)}-${pad2(cst.getUTCDate())}`;
  const clock = withSeconds
    ? `${pad2(cst.getUTCHours())}:${pad2(cst.getUTCMinutes())}:${pad2(cst.getUTCSeconds())}`
    : `${pad2(cst.getUTCHours())}:${pad2(cst.getUTCMinutes())}`;
  return `${date} ${clock}`;
}

export function formatJobDateTime(v?: string | null): string {
  if (!v) return "—";
  const ms = parseJobInstant(String(v));
  if (ms == null) {
    const s = String(v).replace("T", " ");
    return s.length >= 19 ? s.slice(0, 19) : s;
  }
  return formatShifted(ms, true);
}

function utcMinute(v: string): string | null {
  const ms = parseJobInstant(v);
  if (ms == null) return null;
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}

/** 自动标题原先按 UTC 写入。列表改成北京时间后，旧标题按创建时间补 8 小时，手改过的不动。 */
export function displayJobTitle(
  title: string | null | undefined,
  seq: number | null | undefined,
  createdAt?: string | null,
): string {
  const text = (title || "").trim();
  if (!text) return "—";
  if (seq == null || !createdAt) return text;
  const utc = utcMinute(createdAt);
  if (!utc || text !== `#${seq} ${utc}`) return text;
  const shown = formatJobDateTime(createdAt);
  if (shown === "—") return text;
  return `#${seq} ${shown.slice(0, 16)}`;
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

function formatElapsedHours(hours: number): string {
  const shown = Math.max(0, hours).toFixed(1);
  const minutes = Math.round(Number(shown) * 60);
  return `${shown} 小时（${minutes} 分钟）`;
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
  if (active != null) return formatElapsedHours(active / 3600);
  const startMs = Date.parse(String(job.started_at || job.created_at || ""));
  if (!Number.isFinite(startMs)) return "—";
  const live = LIVE.has(job.status || "");
  const endMs = live ? nowMs : Date.parse(String(job.finished_at || ""));
  if (!Number.isFinite(endMs)) return "—";
  const hours = Math.max(0, (endMs - startMs) / 3_600_000);
  return formatElapsedHours(hours);
}
