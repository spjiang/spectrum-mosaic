import type { PushbroomFlight, PushbroomJob } from "../../api";

/** 与推扫 worker 的 pb_mosaic.stages 保持一致 */
export const PB_STAGES: { id: string; label: string }[] = [
  { id: "P0_check", label: "核对三件套" },
  { id: "P1_trajectory", label: "轨迹与时间" },
  { id: "P2_strips", label: "切航带" },
  { id: "P3_align", label: "航带配准" },
  { id: "P4_mosaic", label: "重采样镶嵌" },
  { id: "P5_products", label: "写成果" },
  { id: "P6_report", label: "报告" },
];

export function stageLabel(id?: string | null): string {
  if (!id) return "—";
  return PB_STAGES.find((s) => s.id === id)?.label || id;
}

export const PB_STATUS: Record<PushbroomJob["status"], { text: string; color: string }> = {
  queued: { text: "排队中", color: "default" },
  running: { text: "运行中", color: "processing" },
  cancelling: { text: "取消中", color: "warning" },
  succeeded: { text: "已完成", color: "success" },
  failed: { text: "失败", color: "error" },
  cancelled: { text: "已取消", color: "default" },
};

export const PB_ACTIVE: PushbroomJob["status"][] = ["queued", "running", "cancelling"];

export function formatBytes(n?: number | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return `${h} 小时 ${m} 分`;
  if (m) return `${m} 分 ${r} 秒`;
  return `${r} 秒`;
}

export function flightSize(f: PushbroomFlight): string {
  if (!f.samples || !f.lines || !f.bands) return "—";
  return `${f.samples} 列 × ${f.lines} 行 × ${f.bands} 波段`;
}

export function wavelengthRange(f: PushbroomFlight): string {
  if (f.wavelength_min == null || f.wavelength_max == null) return "—";
  return `${f.wavelength_min.toFixed(1)}–${f.wavelength_max.toFixed(1)} nm`;
}

/** "1-10, 50, 208" → [1..10, 50, 208]。格式不对时抛出带说明的错误。 */
export function parseBandList(text: string, total?: number): number[] {
  const out = new Set<number>();
  for (const raw of text.split(/[,，\s]+/)) {
    const part = raw.trim();
    if (!part) continue;
    const m = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!m) throw new Error(`无法识别“${part}”，请写成 1-10,50 这样的格式`);
    const a = Number(m[1]);
    const b = m[2] ? Number(m[2]) : a;
    if (a < 1 || b < a) throw new Error(`范围“${part}”不对，序号从 1 开始且前小后大`);
    if (total && b > total) throw new Error(`序号 ${b} 超出范围（共 ${total} 个波段）`);
    for (let i = a; i <= b; i += 1) out.add(i);
  }
  if (!out.size) throw new Error("请至少填写一个波段");
  return [...out].sort((x, y) => x - y);
}

/** [1,2,3,5] → "1-3, 5" */
export function formatBandList(bands?: number[]): string {
  if (!bands?.length) return "全部波段";
  const parts: string[] = [];
  let start = bands[0];
  let prev = bands[0];
  for (const b of [...bands.slice(1), Number.NaN]) {
    if (b === prev + 1) {
      prev = b;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}-${prev}`);
    start = b;
    prev = b;
  }
  return `${parts.join(", ")}（${bands.length} 个）`;
}
