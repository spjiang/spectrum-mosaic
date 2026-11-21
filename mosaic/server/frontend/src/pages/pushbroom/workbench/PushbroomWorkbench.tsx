import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Alert, Card, Typography, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomJob, type PushbroomStatus } from "../../../api";
import { useAuth } from "../../../auth";
import { PB_ACTIVE } from "../labels";
import FlightList from "./FlightList";
import FlightJobsModal from "./FlightJobsModal";
import PushbroomJobDetailModal from "./PushbroomJobDetailModal";
import PushbroomJobViewModal from "./PushbroomJobViewModal";

const POLL_MS = 3000;

function baseName(path: string) {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

/** 推扫工作台：架次与任务表格 + 操作列弹窗（同框幅拼图） */
export default function PushbroomWorkbenchPage() {
  const { roles } = useAuth();
  const admin = roles.includes("admin");
  const loc = useLocation();
  const navigate = useNavigate();
  const flightName = useMemo(() => new URLSearchParams(loc.search).get("flight"), [loc.search]);
  const jobParam = useMemo(() => new URLSearchParams(loc.search).get("job"), [loc.search]);
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [flights, setFlights] = useState<PushbroomFlightItem[]>([]);
  const [extra, setExtra] = useState<PushbroomFlightItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState<PushbroomJob[]>([]);
  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<string>("files");
  const [previewJobId, setPreviewJobId] = useState<string | null>(null);

  const all = useMemo(
    () => [...flights, ...extra.filter((e) => !flights.some((f) => f.input_dir === e.input_dir))],
    [flights, extra],
  );
  const flight = all.find((f) => f.name === flightName && f.ok) ?? null;

  const select = useCallback(
    (next: { flight?: string | null; job?: string | null }) => {
      const q = new URLSearchParams(loc.search);
      for (const [key, value] of Object.entries(next)) {
        if (value) q.set(key, value);
        else q.delete(key);
      }
      const search = q.toString();
      navigate({ pathname: loc.pathname, search }, { replace: true });
    },
    [loc.pathname, loc.search, navigate],
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

  const refresh = useCallback(() => {
    loadFlights(true).catch(() => undefined);
    loadJobs().catch(() => undefined);
  }, [loadFlights, loadJobs]);

  useEffect(() => {
    loadFlights().catch(() => undefined);
  }, [loadFlights]);

  useEffect(() => {
    loadJobs().catch((err) => message.error((err as Error).message || String(err)));
  }, [loadJobs]);

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
    const name = baseName(job.input_dir);
    select({ flight: name, job: job.id });
    loadFlights(true).catch(() => undefined);
  }

  function onChecked(item: PushbroomFlightItem) {
    setExtra((cur) => [...cur.filter((e) => e.input_dir !== item.input_dir), item]);
    select({ flight: item.name, job: null });
  }

  function openDetail(id: string, tab = "params") {
    setDetailTab(tab);
    setDetailJobId(id);
  }

  return (
    <div className="pb-wb pb-wb--stack">
      {status?.worker_online === false && (
        <Alert
          type="warning"
          showIcon
          message="推扫计算服务未在线"
          description="任务可以提交，会在队列里等待。请联系管理员启动推扫计算服务。"
        />
      )}
      <Card className="mosaic-panel" title="架次列表">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          点架次名称、单击表格行或「操作 → 任务列表」打开居中弹窗查看历次任务；提交拼图、改选配置等在操作菜单中。
        </Typography.Paragraph>
        <FlightList
          items={all}
          selected={flight?.name ?? null}
          loading={loading}
          admin={admin}
          onSelect={(name) => select({ flight: name, job: null })}
          onReload={() => loadFlights().catch(() => undefined)}
          onChecked={onChecked}
          onSubmitted={onSubmitted}
          onMountChanged={() => loadFlights(true).catch(() => undefined)}
        />
      </Card>

      {flightName ? (
        <FlightJobsModal
          flightName={flightName}
          flight={flight}
          loadingFlights={loading}
          jobs={jobs}
          open
          onClose={() => select({ flight: null, job: null })}
          onDetail={(id) => openDetail(id, "params")}
          onPreview={(id) => setPreviewJobId(id)}
          onDetailFiles={(id) => openDetail(id, "files")}
          onChanged={refresh}
        />
      ) : null}

      <PushbroomJobDetailModal
        jobId={detailJobId}
        open={Boolean(detailJobId)}
        defaultTab={detailTab}
        onClose={() => setDetailJobId(null)}
      />
      <PushbroomJobViewModal
        jobId={previewJobId}
        flightName={flight?.name ?? ""}
        admin={admin}
        open={Boolean(previewJobId)}
        onClose={() => setPreviewJobId(null)}
        onChanged={refresh}
      />
    </div>
  );
}