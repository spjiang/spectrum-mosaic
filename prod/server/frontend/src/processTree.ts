export type Proc = {
  pid: number;
  ppid?: number | null;
  state?: string;
  threads?: number;
  rss_mb?: number | null;
  cmdline?: string;
  comm?: string;
  depth?: number;
  child_count?: number;
  synthetic?: boolean;
  tasks?: { tid: number; comm?: string; state?: string; main?: boolean }[];
};

const ORPHAN_ROOT_PID = -1;

export function fmtMb(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${Number(v).toFixed(1)} MB`;
}

export function processTree(rows: Proc[]): (Proc & { depth: number; child_count: number })[] {
  const byPid = new Map(rows.map((r) => [r.pid, r]));
  const kids = new Map<number, number[]>();
  for (const r of rows) {
    if (r.ppid != null && byPid.has(r.ppid) && r.ppid !== r.pid) {
      const list = kids.get(r.ppid) || [];
      list.push(r.pid);
      kids.set(r.ppid, list);
    }
  }
  const seen = new Set<number>();
  const out: (Proc & { depth: number; child_count: number })[] = [];
  const walk = (pid: number, depth: number) => {
    if (seen.has(pid) || !byPid.has(pid)) return;
    seen.add(pid);
    const node = byPid.get(pid)!;
    const children = (kids.get(pid) || []).sort((a, b) => a - b);
    out.push({ ...node, depth, child_count: node.child_count ?? children.length });
    children.forEach((c) => walk(c, depth + 1));
  };
  rows
    .filter((r) => r.ppid == null || !byPid.has(r.ppid) || r.ppid === r.pid)
    .sort((a, b) => a.pid - b.pid)
    .forEach((r) => walk(r.pid, 0));
  rows.forEach((r) => {
    if (!seen.has(r.pid)) walk(r.pid, 0);
  });
  return out;
}

export function isWorkerHelper(p: Proc, workerPid?: number) {
  const cmd = String(p.cmdline || "");
  const comm = String(p.comm || "");
  if (workerPid && p.pid === workerPid) return true;
  if (cmd.includes("worker_main")) return true;
  if (cmd.includes("resource_tracker") || comm.includes("resource_tracker")) return true;
  if (cmd.includes("docker-init") || comm === "docker-init" || comm === "tini") return true;
  return false;
}

function nestUnderRoot(rows: Proc[], root: Proc): (Proc & { depth: number; child_count: number })[] {
  const ids = new Set(rows.map((r) => r.pid));
  const orphans = rows.filter((r) => r.ppid == null || !ids.has(r.ppid));
  if (orphans.length < 2 && rows.every((r) => r.ppid == null || !ids.has(r.ppid))) {
    if (orphans.length <= 1) return processTree(rows);
  }
  if (!orphans.length) return processTree(rows);
  const children = orphans.map((p) => ({ ...p, ppid: root.pid }));
  const kept = rows.filter((r) => !orphans.some((o) => o.pid === r.pid));
  return processTree([...kept, { ...root, ppid: root.ppid ?? null }, ...children]);
}

/** 包装进程被杀后，计算进程会挂到 init 下，这里补回「父进程 + 缩进子进程」树。 */
export function nestOrphanCompute(all: Proc[], workerPid?: number) {
  const compute = all.filter((p) => !isWorkerHelper(p, workerPid));
  return nestUnderRoot(compute, {
    pid: ORPHAN_ROOT_PID,
    ppid: null,
    state: "—",
    threads: 0,
    rss_mb: 0,
    cmdline: "任务进程（包装进程已退出，子进程仍在计算）",
    comm: "job-tree",
    synthetic: true,
    tasks: [],
  });
}

/** 全部进程：把挂到 docker-init 的计算进程收回到 Worker 下面，恢复原来的层级。 */
export function restoreOrphanHierarchy(all: Proc[], workerPid?: number) {
  const init = all.find((p) => p.pid === 1 || String(p.cmdline || "").includes("docker-init"));
  const initPid = init?.pid;
  const orphans = all.filter((p) => {
    if (isWorkerHelper(p, workerPid)) return false;
    return p.ppid === initPid || p.ppid === 1;
  });
  if (!orphans.length) return processTree(all);
  const rest = all.filter((p) => !orphans.some((o) => o.pid === p.pid));
  const root: Proc = {
    pid: ORPHAN_ROOT_PID,
    ppid: workerPid ?? initPid ?? null,
    state: "—",
    threads: 0,
    rss_mb: 0,
    cmdline: "任务进程（包装进程已退出，子进程仍在计算）",
    comm: "job-tree",
    synthetic: true,
    tasks: [],
  };
  return processTree([...rest, root, ...orphans.map((p) => ({ ...p, ppid: ORPHAN_ROOT_PID }))]);
}

export function computeProcesses(all: Proc[], workerPid?: number) {
  return nestOrphanCompute(all, workerPid);
}

function pack(processes: Proc[]) {
  const real = processes.filter((p) => !p.synthetic);
  return {
    processes,
    process_count: real.length,
    process_rss_mb: Number(real.reduce((s, p) => s + (p.rss_mb || 0), 0).toFixed(1)),
  };
}

export function processesForJob(
  jobPid: number | undefined,
  jobId: string | undefined,
  workerPid: number | undefined,
  all: Proc[],
) {
  const needles = jobId ? [jobId, jobId.slice(0, 8)].filter(Boolean) : [];
  const keep = new Set<number>();
  const roots: number[] = [];
  if (typeof jobPid === "number" && jobPid > 0) {
    if (workerPid != null && jobPid === workerPid) {
      for (const p of all) {
        if (p.ppid === workerPid && !isWorkerHelper(p, workerPid)) roots.push(p.pid);
      }
    } else {
      roots.push(jobPid);
    }
  }
  roots.forEach((pid) => keep.add(pid));
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of all) {
      if (!keep.has(p.pid) && p.ppid != null && keep.has(p.ppid)) {
        keep.add(p.pid);
        grew = true;
      }
    }
  }
  return processTree(
    all.filter((p) => {
      if (keep.has(p.pid)) return true;
      const cmd = String(p.cmdline || "");
      return needles.some((n) => cmd.includes(n));
    }),
  );
}

export function attachJobProcesses(job: { id: string }, inspect: any) {
  const w = inspect?.worker;
  const current = w?.current_job;
  const all: Proc[] = w?.processes || [];
  const workerPid = w?.pid;
  const jobId = String(job.id);
  if (current && String(current.job_id) === jobId) {
    let processes = processesForJob(current.pid, jobId, workerPid, all);
    if (!processes.length && (current.orphan_compute || !current.pid)) {
      processes = computeProcesses(all, workerPid);
    }
    return pack(processes);
  }
  const running = (inspect?.active_jobs || []).filter((j: any) => j.status === "running");
  if (running.length === 1 && String(running[0].id) === jobId) {
    return pack(computeProcesses(all, workerPid));
  }
  return pack([]);
}

const LIVE_JOB = ["queued", "running", "paused", "awaiting_continue"];

export type ComputeState = "live" | "orphan" | "stale" | "none";

export function jobComputeState(job: { id?: string; status?: string }, inspect: any): ComputeState {
  if (!LIVE_JOB.includes(String(job?.status || ""))) return "none";
  const id = String(job.id || "");
  const fromActive = (inspect?.active_jobs || []).find((j: any) => String(j.id) === id)?.compute_state;
  if (fromActive === "live" || fromActive === "orphan" || fromActive === "stale") return fromActive;
  const current = inspect?.worker?.current_job;
  if (current && String(current.job_id) === id) {
    if (current.orphan_compute || !current.pid) return "orphan";
    return "live";
  }
  return "stale";
}
