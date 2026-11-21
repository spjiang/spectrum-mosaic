import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Card, Empty, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomJob, type PushbroomStatus } from "../../../api";
import { useAuth } from "../../../auth";
import { PB_ACTIVE } from "../labels";
import FlightHeader from "./FlightHeader";
import FlightList from "./FlightList";
import JobHistory from "./JobHistory";
import JobMain from "./JobMain";
import JobTabs from "./JobTabs";

const POLL_MS = 3000;

function baseName(path: string) {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

export default function PushbroomWorkbenchPage() {
  const { roles } = useAuth();
  const admin = roles.includes("admin");
  const [params, setParams] = useSearchParams();
  const flightName = params.get("flight");
  const jobParam = params.get("job");
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [flights, setFlights] = useState<PushbroomFlightItem[]>([]);
  const [extra, setExtra] = useState<PushbroomFlightItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState<PushbroomJob[]>([]);
  const [detail, setDetail] = useState<PushbroomJob | null>(null);

  const all = useMemo(
    () => [...flights, ...extra.filter((e) => !flights.some((f) => f.input_dir === e.input_dir))],
    [flights, extra],
  );
  const flight = all.find((f) => f.name === flightName && f.ok) ?? null;
  const selectedId = jobs.some((j) => j.id === jobParam) ? jobParam : (jobs[0]?.id ?? null);
  const shown = (detail?.id === selectedId ? detail : jobs.find((j) => j.id === selectedId)) ?? null;

  const select = useCallback(
    (next: { flight?: string | null; job?: string | null }) => {
      setParams(
        (cur) => {
          const q = new URLSearchParams(cur);
          for (const [key, value] of Object.entries(next)) {
            if (value) q.set(key, value);
            else q.delete(key);
          }
          return q;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const loadFlights = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [st, data] = await Promise.all([api.pushbroomStatus(), api.pushbroomFlights()]);
      setStatus(st);
      setFlights(data.items);
    } catch (err) {
      if (!quiet) message.error((err as Error).message || String(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    if (!flight) {
      setJobs([]);
      return;
    }
    const data = await api.pushbroomJobs({ inputDir: flight.input_dir, pageSize: 50 });
    setJobs(data.items);
  }, [flight?.input_dir]);

  const loadDetail = useCallback(async () => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetail(await api.pushbroomJob(selectedId));
  }, [selectedId]);

  const refresh = useCallback(() => {
    loadFlights(true).catch(() => undefined);
    loadJobs().catch(() => undefined);
    loadDetail().catch(() => undefined);
  }, [loadFlights, loadJobs, loadDetail]);

  useEffect(() => {
    loadFlights().catch(() => undefined);
  }, [loadFlights]);

  useEffect(() => {
    setDetail(null);
    loadJobs().catch((err) => message.error((err as Error).message || String(err)));
  }, [loadJobs]);

  useEffect(() => {
    loadDetail().catch(() => undefined);
  }, [loadDetail]);

  // 只有任务链接时，按任务找到架次补进地址
  useEffect(() => {
    if (flightName || !jobParam) return;
    api
      .pushbroomJob(jobParam)
      .then((j) => select({ flight: baseName(j.input_dir), job: j.id }))
      .catch(() => select({ job: null }));
  }, [flightName, jobParam, select]);

  const live =
    flights.some((f) => f.latest_job && PB_ACTIVE.includes(f.latest_job.status)) || jobs.some((j) => PB_ACTIVE.includes(j.status));
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [live, refresh]);

  function onSubmitted(job: PushbroomJob) {
    setJobs((cur) => [job, ...cur.filter((j) => j.id !== job.id)]);
    select({ job: job.id });
    loadFlights(true).catch(() => undefined);
  }

  function onChecked(item: PushbroomFlightItem) {
    setExtra((cur) => [...cur.filter((e) => e.input_dir !== item.input_dir), item]);
    select({ flight: item.name, job: null });
  }

  return (
    <div className="pb-wb">
      {status?.worker_online === false && (
        <Alert
          type="warning"
          showIcon
          message="推扫计算服务未在线"
          description="任务可以提交，会在队列里等待。请联系管理员启动推扫计算服务。"
        />
      )}
      <div className="pb-wb-body">
        <aside className="pb-wb-side mosaic-panel">
          <FlightList
            items={all}
            selected={flight?.name ?? null}
            loading={loading}
            onSelect={(name) => select({ flight: name, job: null })}
            onReload={() => loadFlights().catch(() => undefined)}
            onChecked={onChecked}
          />
        </aside>
        <section className="pb-wb-main">
          {!flight ? (
            <Card className="mosaic-panel pb-wb-empty">
              <Empty description="在左边选一个架次" />
            </Card>
          ) : (
            <>
              <FlightHeader
                key={flight.input_dir}
                flight={flight}
                admin={admin}
                onSubmitted={onSubmitted}
                onMountChanged={() => loadFlights(true).catch(() => undefined)}
              />
              <div className="pb-wb-mid">
                <Card className="mosaic-panel pb-wb-stage">
                  <JobMain job={shown} admin={admin} flightName={flight.name} onChanged={refresh} />
                </Card>
                <Card className="mosaic-panel pb-wb-history" title={`历次任务（${jobs.length}）`}>
                  <JobHistory jobs={jobs} selectedId={selectedId} onSelect={(id) => select({ job: id })} onChanged={refresh} />
                </Card>
              </div>
              {shown && (
                <Card className="mosaic-panel">
                  <JobTabs job={shown} />
                </Card>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}