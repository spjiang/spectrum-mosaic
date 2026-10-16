import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";

const LIVE = new Set(["queued", "running", "paused", "awaiting_continue"]);

type JobsCtx = {
  jobs: any[];
  loading: boolean;
  refresh: () => Promise<void>;
  activeJob: any | null;
};

const Ctx = createContext<JobsCtx | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [jobs, setJobs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const inflight = useRef(false);
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;

  const refresh = useCallback(async () => {
    if (!token || inflight.current) return;
    inflight.current = true;
    setLoading(true);
    try {
      setJobs(await api.jobs());
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) {
      setJobs([]);
      return;
    }
    let stopped = false;
    const tick = async () => {
      if (stopped || inflight.current || document.hidden) return;
      inflight.current = true;
      try {
        const rows = await api.jobs();
        if (!stopped) setJobs(rows);
      } catch {
        /* 后台刷新失败时保留库里上一份 */
      } finally {
        inflight.current = false;
      }
    };
    tick();
    const t = setInterval(() => {
      if (jobsRef.current.some((j) => LIVE.has(j.status))) tick();
    }, 4000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [token]);

  const activeJob = useMemo(() => jobs.find((j) => LIVE.has(j.status)) || null, [jobs]);
  const value = useMemo(
    () => ({ jobs, loading, refresh, activeJob }),
    [jobs, loading, refresh, activeJob]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useJobs() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useJobs 必须在 JobsProvider 内");
  return ctx;
}
