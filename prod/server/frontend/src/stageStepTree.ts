import type { StageStep } from "./api";

export type StepNode = StageStep & { children?: StepNode[] };

const BA_CHILD = /^(前方交会|平差阶段\d+)$/;

function seconds(step: StageStep): number {
  return step.elapsed_s != null && Number.isFinite(step.elapsed_s) ? step.elapsed_s : 0;
}

function rankBa(name: string): number {
  if (name === "前方交会") return 0;
  const stage = /^平差阶段(\d+)$/.exec(name);
  return stage ? Number(stage[1]) : 9;
}

/** 同一名字里耗时不到 1 秒的那一行是开工说明，并进真正干活的那一行。 */
function foldAnnouncements(steps: StageStep[]): StageStep[] {
  const out: StageStep[] = [];
  for (const step of steps) {
    const prev = out[out.length - 1];
    const copy = { ...step };
    if (prev && prev.name === copy.name && (prev.gpu || copy.gpu)) {
      prev.gpu = true;
      copy.gpu = true;
    }
    if (prev && prev.name === copy.name) {
      const prevShort = seconds(prev) < 1;
      const copyShort = seconds(copy) < 1;
      if (prevShort !== copyShort) {
        const detail = [prev.detail, copy.detail].filter(Boolean).join(" · ");
        if (prevShort) {
          prev.detail = detail;
          prev.elapsed_s = copy.elapsed_s;
          prev.status = copy.status;
        } else {
          prev.detail = detail;
        }
        continue;
      }
    }
    out.push(copy);
  }
  return out;
}

function sumStatus(children: StageStep[], fallback: StageStep["status"]): StageStep["status"] {
  return children.some((step) => step.status === "running") ? "running" : fallback;
}

function baGroup(steps: StageStep[]): StepNode[] {
  const children: StageStep[] = [];
  let summary: StageStep | null = null;
  let at = -1;
  const rest: StageStep[] = [];
  steps.forEach((step) => {
    if (BA_CHILD.test(step.name)) {
      if (at < 0) at = rest.length;
      children.push(step);
      return;
    }
    if (step.name === "平差") {
      summary = step;
      if (at < 0) at = rest.length;
      return;
    }
    rest.push(step);
  });
  if (!children.length && !summary) return rest.map((step) => ({ ...step }));
  children.sort((a, b) => rankBa(a.name) - rankBa(b.name));
  const parent: StepNode = {
    name: "平差 · 稀疏重建合计",
    detail: "等于下面几步相加",
    elapsed_s: children.length ? children.reduce((sum, step) => sum + seconds(step), 0) : summary?.elapsed_s ?? null,
    status: sumStatus(children, summary?.status || "done"),
    children: children.map((step) => ({ ...step })),
  };
  const out = rest.map((step) => ({ ...step }));
  out.splice(at < 0 ? out.length : at, 0, parent);
  return out;
}

function orthoGroup(steps: StepNode[]): StepNode[] {
  const compute = steps.find((step) => step.name === "多光谱正射");
  const bands = steps.filter((step) => step.name.startsWith("正射 ") && step.name !== "正射 Color");
  if (!compute || !bands.length) return steps;
  const children: StepNode[] = [{ ...compute, name: "共同计算" }, ...bands.map((step) => ({ ...step }))];
  const parent: StepNode = {
    name: "多光谱正射合计",
    detail: "等于下面几步相加",
    elapsed_s: children.reduce((sum, step) => sum + seconds(step), 0),
    status: sumStatus(children, "done"),
    children,
  };
  const out: StepNode[] = [];
  let placed = false;
  for (const step of steps) {
    if (step.name === "多光谱正射" || (step.name.startsWith("正射 ") && step.name !== "正射 Color")) {
      if (!placed) {
        out.push(parent);
        placed = true;
      }
      continue;
    }
    out.push(step);
  }
  return out;
}

/** 合计行收成上一级，被它覆盖的步骤收成下一级。上一级的时间就是下一级相加。 */
export function nestStageSteps(steps: StageStep[]): StepNode[] {
  return orthoGroup(baGroup(foldAnnouncements(steps)));
}

export function stageUsedGpu(steps: StageStep[]): boolean {
  const walk = (node: StepNode): boolean => Boolean(node.gpu) || (node.children || []).some(walk);
  return nestStageSteps(steps).some(walk);
}
