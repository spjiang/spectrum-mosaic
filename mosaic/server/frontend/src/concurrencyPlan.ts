/** 按测区实测像幅、波段和航线范围估算并发。数字与 worker 的单进程估计同一套。 */

export type SceneFacts = {
  width: number;
  height: number;
  nColor: number;
  nMsBands: number;
  nativeGsdM: number;
  spanXM: number | null;
  spanYM: number | null;
};

export type PlanRow = {
  id: string;
  label: string;
  perGb: number;
  slots: number;
  /** 写出时的波段总数。slots 是同时能写的路数。 */
  of?: number;
  bound: "cpu" | "memory";
  unit: "进程" | "波段" | "同时";
};

const CACHE_PER_WORKER = 48;
const TILE_WORK_MB = 640;
const AT_BASE_PIXELS = 2048 * 1536;
const AT_BASE_MB = 450;
/** 与 worker 的 headroom_mb 相同：单进程估计再留四分之一。 */
const HEADROOM_NUM = 5;
const HEADROOM_DEN = 4;
const AT_RESERVE_GB = 1;
const RESERVE_GB = 1.5;
const MS_IMAGES_PER_BAND = 24;
/** 空闲内存充裕时，任务预算取空闲的这一比例，其余留给系统和页面。 */
const FREE_MEMORY_SHARE = 0.8;
/** 单波段写出：acc/wsum/labels/label_w 再加写出临时量。Color 累加器留在内存里也是每像元 22 字节。 */
const WRITE_BYTES = 22;

function ceilDiv(n: number, d: number): number {
  return Math.floor((Math.max(0, n) + d - 1) / d);
}

function cacheMb(imageBytes: number, cache: number, workMb = TILE_WORK_MB): number {
  return ceilDiv(cache * imageBytes, 2 ** 20) + workMb;
}

function headroomMb(mb: number): number {
  return Math.ceil((mb * HEADROOM_NUM) / HEADROOM_DEN);
}

function msCacheSlots(nBands: number): number {
  const n = Math.max(1, nBands);
  const per = n <= 8 ? MS_IMAGES_PER_BAND : Math.max(4, Math.floor((MS_IMAGES_PER_BAND * 8) / n));
  return per * n;
}

export function formatGb(n: number): string {
  const text = (Math.round(n * 100) / 100).toFixed(2);
  return text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export function formatPixels(n: number): string {
  if (n >= 10000) return `${formatGb(n / 10000)} 万`;
  return String(Math.round(n));
}

function orthoPixels(scene: SceneFacts, orthoScale: number): { width: number; height: number } | null {
  if (scene.spanXM == null || scene.spanYM == null || scene.nativeGsdM <= 0) return null;
  const gsd = scene.nativeGsdM * Math.max(1, orthoScale);
  return {
    width: Math.max(1, Math.ceil(scene.spanXM / gsd)),
    height: Math.max(1, Math.ceil(scene.spanYM / gsd)),
  };
}

type Costs = {
  atMb: number;
  denseMb: number;
  colorMb: number;
  msMb: number;
  writeMb: number;
  holdMb: number;
  pixels: { width: number; height: number } | null;
};

export function sceneCosts(scene: SceneFacts, orthoScale: number): Costs {
  const pixels = Math.max(1, scene.width) * Math.max(1, scene.height);
  const atMb = headroomMb(Math.max(256, Math.round((AT_BASE_MB * pixels) / AT_BASE_PIXELS)));
  const denseMb = headroomMb(cacheMb(pixels, CACHE_PER_WORKER));
  const colorMb = headroomMb(cacheMb(pixels * 3, CACHE_PER_WORKER));
  const msMb = scene.nMsBands > 0 ? headroomMb(cacheMb(pixels * 2, msCacheSlots(scene.nMsBands))) : 0;
  const frame = orthoPixels(scene, orthoScale);
  const cells = frame ? frame.width * frame.height : 0;
  const cellMb = cells > 0 ? headroomMb(ceilDiv(cells * WRITE_BYTES, 2 ** 20)) : 0;
  return { atMb, denseMb, colorMb, msMb, writeMb: cellMb, holdMb: cellMb, pixels: frame };
}

/** 4 核及以下留 1 核给系统和页面，再多留 2 核。占满后整机会卡住。 */
export function hostReserve(visible: number): number {
  const n = Math.max(0, Math.round(visible));
  if (n <= 1) return 0;
  if (n <= 4) return 1;
  return 2;
}

export function usableCores(visible: number | null): number | null {
  if (visible == null || visible <= 0) return null;
  const n = Math.round(visible);
  return Math.max(1, n - hostReserve(n));
}

function fitSlots(memoryGb: number, cap: number, perMb: number, reserveGb: number, holdMb = 0): number {
  const leftoverMb = memoryGb * 1024 - reserveGb * 1024 - holdMb;
  const byMem = leftoverMb < perMb ? 1 : Math.floor(leftoverMb / perMb);
  return Math.max(1, Math.min(Math.max(1, cap), Math.max(1, byMem)));
}

export function planRows(scene: SceneFacts, memoryGb: number, cpus: number, orthoScale: number): PlanRow[] {
  const costs = sceneCosts(scene, orthoScale);
  const cpu = Math.max(1, Math.round(cpus));
  const rows: PlanRow[] = [
    row("at", "空三", costs.atMb, fitSlots(memoryGb, cpu, costs.atMb, AT_RESERVE_GB), cpu, "进程"),
    row("dense", "密集匹配", costs.denseMb, fitSlots(memoryGb, cpu, costs.denseMb, RESERVE_GB), cpu, "进程"),
    row("color", "Color 正射", costs.colorMb, fitSlots(memoryGb, cpu, costs.colorMb, RESERVE_GB), cpu, "进程"),
  ];
  if (scene.nMsBands > 0 && costs.msMb > 0) {
    rows.push(row("ms", "多光谱正射", costs.msMb, fitSlots(memoryGb, cpu, costs.msMb, RESERVE_GB), cpu, "进程"));
  }
  if (scene.nMsBands > 0 && costs.writeMb > 0) {
    const slots = fitSlots(memoryGb, scene.nMsBands, costs.writeMb, RESERVE_GB, costs.holdMb);
    const together = slots >= scene.nMsBands;
    rows.push({
      id: "write",
      label: "多光谱写出",
      perGb: costs.writeMb / 1024,
      slots,
      of: scene.nMsBands,
      bound: together ? "cpu" : "memory",
      unit: together ? "波段" : "同时",
    });
  }
  return rows;
}

function row(id: string, label: string, perMb: number, slots: number, cpu: number, unit: PlanRow["unit"]): PlanRow {
  return {
    id,
    label,
    perGb: perMb / 1024,
    slots,
    bound: slots >= cpu ? "cpu" : "memory",
    unit,
  };
}

/** 以前没读空闲内存时，页面用 36 GB 当缺省。这个数不再当作有意配置。 */
const LEGACY_MEMORY_GB = 36;

/** 方案没写内存，或仍是旧的 36 GB 时，改用空闲内存的 80%。 */
export function initialMemoryGb(savedGb: number, freeGb: number | null, ceilingGb: number | null): number {
  const suggested = memoryFromFree(freeGb, ceilingGb);
  const saved = savedGb > 0 ? savedGb : 0;
  const legacy = saved > 0 && Math.abs(saved - LEGACY_MEMORY_GB) < 0.05;
  if (suggested != null && (saved <= 0 || legacy)) return suggested;
  if (saved <= 0) return 0;
  if (ceilingGb != null && ceilingGb > 0) return Math.min(saved, ceilingGb);
  return saved;
}

/** 节点可见核数的 80%。多出来的核不会多开进程，内存养不住时路数会再降。 */
export function cpusFromShare(visible: number | null): number | null {
  if (visible == null || !(visible > 0)) return null;
  const n = Math.round(visible);
  return Math.max(1, Math.min(n, Math.floor(n * FREE_MEMORY_SHARE)));
}

/** 方案没写核数，或写成了占满 / 只留 2 核时，改用节点核数的 80%。 */
export function initialCpu(saved: number, visible: number | null): number {
  const suggested = cpusFromShare(visible);
  const n = saved > 0 ? Math.round(saved) : 0;
  if (suggested == null) return n;
  const ceiling = Math.round(visible ?? 0);
  const legacy = n <= 0 || (ceiling > 0 && n >= ceiling - hostReserve(ceiling));
  if (legacy) return suggested;
  return Math.min(n, ceiling > 0 ? ceiling : n);
}

/** 空闲内存的 80%，不超过节点可见上限。读不到空闲时返回空。 */
export function memoryFromFree(freeGb: number | null, ceilingGb: number | null): number | null {
  if (freeGb == null || !(freeGb > 0)) return null;
  let gb = freeGb * FREE_MEMORY_SHARE;
  if (ceilingGb != null && ceilingGb > 0) gb = Math.min(gb, ceilingGb);
  return Math.max(1, Math.round(gb * 10) / 10);
}

export function advisedSetting(
  scene: SceneFacts,
  orthoScale: number,
  memoryCeilingGb: number | null,
  cpuCeiling: number | null,
  freeMemoryGb: number | null = null,
): { cpus: number; memoryGb: number } {
  const costs = sceneCosts(scene, orthoScale);
  const writeTogetherMb =
    costs.writeMb > 0 && scene.nMsBands > 0
      ? RESERVE_GB * 1024 + costs.holdMb + scene.nMsBands * costs.writeMb
      : RESERVE_GB * 1024 + Math.max(costs.colorMb, costs.atMb) * 4;
  let memoryGb = Math.max(1, Math.ceil(writeTogetherMb / 1024));
  const share = memoryFromFree(freeMemoryGb, memoryCeilingGb);
  if (share != null) memoryGb = Math.max(memoryGb, share);
  if (memoryCeilingGb != null && memoryCeilingGb > 0) memoryGb = Math.min(memoryGb, memoryCeilingGb);
  memoryGb = Math.round(memoryGb * 10) / 10;
  const shareCpu = cpusFromShare(cpuCeiling);
  if (shareCpu != null) {
    return { cpus: Math.max(1, Math.min(shareCpu, Math.max(1, scene.nColor))), memoryGb };
  }
  const byAt = fitSlots(memoryGb, 10_000, costs.atMb, AT_RESERVE_GB);
  let cpus = Math.min(byAt, Math.max(1, scene.nColor));
  const usable = usableCores(cpuCeiling);
  if (usable != null) cpus = Math.min(cpus, usable);
  return { cpus: Math.max(1, cpus), memoryGb };
}

export function planAdvice(
  scene: SceneFacts,
  memoryGb: number,
  cpus: number,
  orthoScale: number,
  memoryCeilingGb: number | null,
  cpuCeiling: number | null = null,
  freeMemoryGb: number | null = null,
): string {
  const costs = sceneCosts(scene, orthoScale);
  const rows = planRows(scene, memoryGb, cpus, orthoScale);
  const write = rows.find((item) => item.id === "write");
  const target = advisedSetting(scene, orthoScale, null, null);
  const advised = advisedSetting(scene, orthoScale, memoryCeilingGb, cpuCeiling, freeMemoryGb);
  const ceiling = memoryCeilingGb != null && memoryCeilingGb > 0 ? memoryCeilingGb : null;
  const shareCpu = cpusFromShare(cpuCeiling);
  const saturated = shareCpu != null && Math.round(cpus) > shareCpu;
  const cpuNote = saturated
    ? `${Math.round(cpus)} 核超过节点的 80%。建议用 ${shareCpu} 核，其余留给系统和页面。内存养不住时，实际进程数会少于核数。`
    : "";
  if (scene.nMsBands <= 0) {
    const base = `这份测区是彩色影像，正射按每个进程 ${formatGb(costs.colorMb / 1024)} GB 安排。建议 ${advised.cpus} 核、${formatGb(advised.memoryGb)} GB。`;
    return cpuNote ? `${cpuNote}${base}` : base;
  }
  if (!costs.pixels || costs.writeMb <= 0) {
    const base = "影像尺寸已经读到。照片里没有位置，正射范围算不出来，写出暂不估算。";
    return cpuNote ? `${cpuNote}${base}` : base;
  }
  const withCpu = (text: string) => (cpuNote ? `${cpuNote}${text}` : text);
  if (ceiling != null && ceiling + 0.05 < target.memoryGb) {
    const slots = write?.slots ?? 1;
    return withCpu(
      `按航线范围，${scene.nMsBands} 个波段一起写出大约要 ${formatGb(target.memoryGb)} GB。本机可见约 ${formatGb(ceiling)} GB，现在大约同时写 ${slots} 个波段。`,
    );
  }
  if (write && write.slots < scene.nMsBands) {
    return withCpu(
      `现在大约同时写 ${write.slots} 个波段。调到 ${formatGb(advised.memoryGb)} GB 后，${scene.nMsBands} 个波段可以一起写出。`,
    );
  }
  if (advised.memoryGb > memoryGb + 0.5 && freeMemoryGb != null && freeMemoryGb > 0) {
    return withCpu(
      `${scene.nMsBands} 个波段可以一起写出。空闲约 ${formatGb(freeMemoryGb)} GB，建议用 ${formatGb(advised.memoryGb)} GB（空闲的 80%），核数 ${advised.cpus}（节点的 80%）。内存养不住的阶段会少开进程。`,
    );
  }
  if (shareCpu != null && Math.abs(Math.round(cpus) - advised.cpus) >= 1) {
    return withCpu(
      `${scene.nMsBands} 个波段可以一起写出。核数按节点的 80% 取 ${advised.cpus}。内存不够时进程数会少于核数，不会因为核多就把内存用爆。`,
    );
  }
  return withCpu(`${scene.nMsBands} 个波段可以一起写出。进程数取核数和内存能养住的路数里较小的那个。`);
}

export type PlanAction = { memoryGb: number; cpus: number; label: string };

/** 按钮只写真正会改的那一项。内存已经等于建议值时，不再把内存写进按钮。 */
export function planAction(
  scene: SceneFacts,
  memoryGb: number,
  cpus: number,
  orthoScale: number,
  memoryCeilingGb: number | null,
  cpuCeiling: number | null,
  freeMemoryGb: number | null = null,
): PlanAction | null {
  const target = advisedSetting(scene, orthoScale, null, null);
  const advised = advisedSetting(scene, orthoScale, memoryCeilingGb, cpuCeiling, freeMemoryGb);
  const rows = planRows(scene, memoryGb, cpus, orthoScale);
  const write = rows.find((item) => item.id === "write");
  const costs = sceneCosts(scene, orthoScale);
  const writeTogether =
    scene.nMsBands <= 0 ||
    costs.writeMb <= 0 ||
    (write != null && write.slots >= scene.nMsBands);
  const memoryOk = Math.abs(memoryGb - advised.memoryGb) < 0.5;
  // 写出与内存已达标时，核数交给上方滑块；底部不再挂默认一键按钮。
  if (writeTogether && memoryOk) {
    return null;
  }
  const shareCpu = cpusFromShare(cpuCeiling);
  const curCpu = Math.max(1, Math.round(cpus));
  const parts: string[] = [];
  if (advised.cpus !== curCpu) {
    if (shareCpu != null && curCpu > shareCpu) {
      parts.push(`改为 ${advised.cpus} 核（节点的 80%）`);
    } else {
      parts.push(`计算改为 ${advised.cpus} 核`);
    }
  }
  if (Math.abs(memoryGb - advised.memoryGb) >= 0.5) {
    const writesAll = scene.nMsBands > 0 && advised.memoryGb + 0.05 >= target.memoryGb;
    parts.push(
      writesAll
        ? `内存调到 ${formatGb(advised.memoryGb)} GB，${scene.nMsBands} 个波段一起写出`
        : `内存调到 ${formatGb(advised.memoryGb)} GB`,
    );
  }
  if (parts.length === 0) return null;
  return { memoryGb: advised.memoryGb, cpus: advised.cpus, label: parts.join("，") };
}
