import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Input,
  Progress,
  Row,
  Select,
  Slider,
  Space,
  Typography,
  message,
} from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { useJobs } from "../jobsContext";
import JobStatusTag from "../components/JobStatusTag";
import JobSnapshot from "../components/JobSnapshot";
import JobActions from "../components/JobActions";
import JobStageFlow from "../components/JobStageFlow";
import { TitleWithProgressHelp } from "../components/ProgressHelpTip";
import LogConsole from "../components/LogConsole";
import { formatNo } from "../ids";
import { canExecute, canViewOps } from "../roles";
import { displayStage, stageLabel } from "../stages";
import { jobComputeState } from "../processTree";

const ACTIVE = ["queued", "running", "paused", "awaiting_continue"];

function formatEta(seconds?: number | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return "不到 1 分钟";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function applyPaths(setInput: (v: string) => void, setOutput: (v: string) => void, values?: Record<string, any>) {
  if (!values) return;
  if (values.input_dir) setInput(String(values.input_dir));
  if (values.output_dir) setOutput(String(values.output_dir));
}

function engineLimitGb(inspect: any): number | null {
  const cgroup = inspect?.worker?.memory?.container_limit_mb;
  const total = inspect?.worker?.host_memory?.mem_total_mb;
  const mb = typeof cgroup === "number" && cgroup > 0 ? cgroup : total;
  if (typeof mb !== "number" || !(mb > 0)) return null;
  return Math.round((mb / 1024) * 10) / 10;
}

function memorySliderMax(limitGb: number | null): number {
  return limitGb != null && limitGb > 0 ? limitGb : 36;
}

function pickMemoryGb(raw: unknown, max: number): number {
  const n = typeof raw === "number" && raw > 0 ? raw : 36;
  return Math.min(n, max);
}

function cpuSliderMax(limit: number | null): number {
  return limit != null && limit > 0 ? limit : 8;
}

function pickCpus(raw: unknown, max: number): number {
  if (typeof raw === "number" && raw > 0) return Math.min(raw, max);
  return 0;
}

function asPositiveNumber(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) return raw;
  if (typeof raw === "string" && raw.trim()) {
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return 0;
}

/** 与任务列表同一口径：列字段优先，否则快照；空/0 = 自动。 */
function jobMemoryGb(job: any): number {
  return asPositiveNumber(job?.memory_gb ?? job?.params_snapshot?.memory_gb);
}

function jobCpus(job: any): number {
  return asPositiveNumber(job?.cpus ?? job?.params_snapshot?.cpus);
}

export default function ExecutePage() {
  const { roles } = useAuth();
  const allowRun = canExecute(roles);
  const showOpsHelp = canViewOps(roles);
  const { activeJob, refresh } = useJobs();
  const [searchParams, setSearchParams] = useSearchParams();
  const watchId = searchParams.get("job");
  const [profiles, setProfiles] = useState<any[]>([]);
  const [profileId, setProfileId] = useState<number | undefined>();
  const [inputDir, setInputDir] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [memoryGb, setMemoryGb] = useState<number | null>(watchId ? 0 : 36);
  const [engineRamGb, setEngineRamGb] = useState<number | null>(null);
  const [cpus, setCpus] = useState<number>(0);
  const [engineCpu, setEngineCpu] = useState<number | null>(null);
  const [job, setJob] = useState<any>(null);
  const [inspect, setInspect] = useState<any>(null);
  const [logs, setLogs] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const logBusy = useRef(false);

  const selected = useMemo(() => profiles.find((p) => p.id === profileId), [profiles, profileId]);

  useEffect(() => {
    Promise.all([api.paramDefs(), api.profiles()])
      .then(([defs, list]) => {
        const defaults: Record<string, any> = {};
        for (const d of defs) defaults[d.key] = d.default_value;
        applyPaths(setInputDir, setOutputDir, defaults);
        setProfiles(list);

        const preferred = list[0];
        if (preferred) {
          setProfileId(preferred.id);
          applyPaths(setInputDir, setOutputDir, { ...defaults, ...(preferred.values || {}) });
          if (!searchParams.get("job")) {
            const gb = preferred.values?.memory_gb ?? defaults.memory_gb;
            setMemoryGb(typeof gb === "number" && gb > 0 ? gb : 36);
            const n = preferred.values?.cpus ?? defaults.cpus;
            setCpus(typeof n === "number" && n > 0 ? n : 0);
          }
        }
      })
      .catch((e) => message.error(String(e)));
    api
      .workerInspect()
      .then((w) => {
        setInspect(w);
        const gb = engineLimitGb(w);
        if (gb != null) setEngineRamGb(gb);
        const n = w?.engine_cpu;
        if (typeof n === "number" && n > 0) setEngineCpu(n);
      })
      .catch(() => {
        /* Worker 未就绪时仍可创建任务 */
      });
  }, []);

  useEffect(() => {
    if (!watchId) {
      setJob(null);
      setLogs("");
      const p = profiles.find((x) => x.id === profileId);
      const gb = p?.values?.memory_gb;
      setMemoryGb(pickMemoryGb(gb, memorySliderMax(engineRamGb)));
      setCpus(pickCpus(p?.values?.cpus, cpuSliderMax(engineCpu)));
      return;
    }
    let cancelled = false;
    api
      .job(watchId)
      .then((j) => {
        if (cancelled) return;
        setJob(j);
        setMemoryGb(jobMemoryGb(j));
        setCpus(jobCpus(j));
      })
      .catch((e: any) => {
        if (!cancelled) {
          setJob(null);
          message.error(e.message || String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [watchId]);

  useEffect(() => {
    if (!job?.id) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${job.id}`);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.job) setJob((j: any) => ({ ...j, ...msg.job, id: j.id }));
      } catch {
        /* ignore */
      }
    };
    wsRef.current = ws;
    const pullLogs = async () => {
      if (logBusy.current) return;
      logBusy.current = true;
      try {
        setLogs(await api.logs(job.id));
      } catch {
        /* 日志在库里，失败时保留已有内容 */
      } finally {
        logBusy.current = false;
      }
    };
    pullLogs();
    const timer = setInterval(() => {
      if (ACTIVE.includes(job.status)) {
        pullLogs();
        api.workerInspect().then(setInspect).catch(() => undefined);
      }
    }, 1000);
    return () => {
      ws.close();
      clearInterval(timer);
    };
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (engineRamGb == null) return;
    setMemoryGb((cur) => (cur && cur > engineRamGb ? engineRamGb : cur));
  }, [engineRamGb]);

  useEffect(() => {
    if (engineCpu == null) return;
    setCpus((cur) => (cur && cur > engineCpu ? engineCpu : cur));
  }, [engineCpu]);

  const summary = selected?.values || {};
  const busy = Boolean(job && ACTIVE.includes(job.status));
  const jobState = job ? jobComputeState(job, inspect) : "none";
  const runningElsewhere = Boolean(activeJob && activeJob.id !== job?.id);
  const memMax = memorySliderMax(engineRamGb);
  const memMarks: Record<number, string> = { 0: "0" };
  if (memMax > 16) memMarks[16] = "16";
  if (memMax > 36) memMarks[36] = "36";
  memMarks[memMax] = String(memMax);
  const cpuMax = cpuSliderMax(engineCpu);
  const cpuMarks: Record<number, string> = { 0: "0" };
  if (cpuMax > 8) cpuMarks[8] = "8";
  cpuMarks[cpuMax] = String(cpuMax);

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={10}>
        <Card className="mosaic-panel" title="新建处理">
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            {!allowRun && (
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                当前账号无任务提交权限，仅可查看进行中的任务。
              </Typography.Paragraph>
            )}
            <div>
              <Typography.Text type="secondary">处理方案</Typography.Text>
              <Select
                placeholder="选择处理方案"
                disabled={!allowRun}
                style={{ width: "100%", marginTop: 6 }}
                options={profiles.map((p) => ({
                  value: p.id,
                  label: `${p.name} (v${p.version})`,
                }))}
                value={profileId}
                onChange={(id) => {
                  setProfileId(id);
                  const p = profiles.find((x) => x.id === id);
                  applyPaths(setInputDir, setOutputDir, p?.values);
                  const gb = p?.values?.memory_gb;
                  setMemoryGb(pickMemoryGb(gb, memMax));
                  setCpus(pickCpus(p?.values?.cpus, cpuMax));
                }}
              />
            </div>
            <div>
              <Typography.Text type="secondary">测区数据</Typography.Text>
              <Input
                style={{ marginTop: 6 }}
                disabled={!allowRun}
                placeholder="选择已挂载的测区目录"
                value={inputDir}
                onChange={(e) => setInputDir(e.target.value)}
              />
            </div>
            <div>
              <Typography.Text type="secondary">成果目录</Typography.Text>
              <Input
                style={{ marginTop: 6 }}
                disabled={!allowRun}
                placeholder="处理完成后写入的目录"
                value={outputDir}
                onChange={(e) => setOutputDir(e.target.value)}
              />
            </div>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <Typography.Text type="secondary">任务内存</Typography.Text>
                <Typography.Text strong>{memoryGb ? `${memoryGb} GB` : "自动"}</Typography.Text>
              </div>
              <div className="memory-slider">
                <Slider
                  min={0}
                  max={memMax}
                  step={0.1}
                  disabled={!allowRun}
                  value={memoryGb ?? 0}
                  onChange={(v) => setMemoryGb(typeof v === "number" ? v : 0)}
                  marks={memMarks}
                  tooltip={{ formatter: (v) => (v ? `${v} GB` : "自动") }}
                />
              </div>
              <Typography.Paragraph type="secondary" style={{ margin: "0 0 0", fontSize: 12, lineHeight: 1.55 }}>
                {showOpsHelp
                  ? `上限为计算节点可见内存（当前 ${engineRamGb != null ? `${engineRamGb} GB` : "未知"}）。0 表示系统自动分配。Docker Desktop Memory Limit 决定上限。`
                  : `为本任务预留的内存。0 表示系统自动分配。本测区建议 36 GB。`}
              </Typography.Paragraph>
            </div>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <Typography.Text type="secondary">任务 CPU</Typography.Text>
                <Typography.Text strong>{cpus ? `${cpus} 核` : "自动"}</Typography.Text>
              </div>
              <div className="memory-slider">
                <Slider
                  min={0}
                  max={cpuMax}
                  step={1}
                  disabled={!allowRun}
                  value={cpus}
                  onChange={(v) => setCpus(typeof v === "number" ? v : 0)}
                  marks={cpuMarks}
                  tooltip={{ formatter: (v) => (v ? `${v} 核` : "自动") }}
                />
              </div>
              <Typography.Paragraph type="secondary" style={{ margin: "0 0 0", fontSize: 12, lineHeight: 1.55 }}>
                {showOpsHelp
                  ? `上限为计算节点可见 CPU（当前 ${engineCpu != null ? `${engineCpu} 核` : "未知"}）。0 表示系统自动分配。`
                  : "为本任务预留的 CPU。0 表示系统自动分配。本测区建议 8 核。"}
              </Typography.Paragraph>
            </div>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
              每次开始处理会在成果目录下自动建立时间戳子目录，不覆盖上次结果。
            </Typography.Paragraph>

            <Collapse
              size="small"
              items={[
                {
                  key: "summary",
                  label: "方案参数摘要",
                  children: (
                    <Descriptions size="small" column={1} bordered>
                      <Descriptions.Item label="bands">{JSON.stringify(summary.bands)}</Descriptions.Item>
                      <Descriptions.Item label="dsm_gsd">{String(summary.dsm_gsd ?? "自动估计")}</Descriptions.Item>
                      <Descriptions.Item label="调试对照目录">
                        {summary.benchmark_dir || "—"}
                      </Descriptions.Item>
                      <Descriptions.Item label="调试套色">
                        {summary.match_reference_color ? "开（不推荐）" : "关"}
                      </Descriptions.Item>
                      <Descriptions.Item label="reuse_dsm">{summary.reuse_dsm || "—"}</Descriptions.Item>
                      <Descriptions.Item label="cache_dir">{summary.cache_dir || "默认"}</Descriptions.Item>
                      <Descriptions.Item label="workers_at">{summary.workers_at ?? "—"}</Descriptions.Item>
                      <Descriptions.Item label="memory_gb">{String(summary.memory_gb ?? 0)}</Descriptions.Item>
                      <Descriptions.Item label="cpus">{String(summary.cpus ?? 0)}</Descriptions.Item>
                    </Descriptions>
                  ),
                },
              ]}
            />

            <Button
              type="primary"
              size="large"
              icon={<PlayCircleOutlined />}
              loading={submitting}
              disabled={!allowRun || busy || runningElsewhere}
              block
              onClick={async () => {
                if (!allowRun) {
                  message.warning("当前账号无任务提交权限");
                  return;
                }
                if (!profileId) {
                  message.warning("请选择处理方案");
                  return;
                }
                if (!inputDir.trim()) {
                  message.warning("输入目录为必填");
                  return;
                }
                if (!outputDir.trim()) {
                  message.warning("输出目录为必填");
                  return;
                }
                if (!inputDir.trim().startsWith("/data") || !outputDir.trim().startsWith("/data")) {
                  message.warning("输入/输出须位于容器 /data 下");
                  return;
                }
                if (memoryGb && engineRamGb && memoryGb > engineRamGb) {
                  message.error(
                    `任务内存 ${memoryGb}GB 超过计算节点上限 ${engineRamGb}GB。请把滑块调到上限以内。`
                  );
                  return;
                }
                setSubmitting(true);
                try {
                  const j = await api.createJob({
                    profile_id: profileId,
                    input_dir: inputDir,
                    output_dir: outputDir,
                    params: { memory_gb: memoryGb ?? 0, cpus: cpus ?? 0 },
                  });
                  setJob(j);
                  setSearchParams({ job: j.id });
                  await refresh();
                  message.success(`已开始处理，成果写入 ${j.output_dir}`);
                  try {
                    setLogs(await api.logs(j.id));
                  } catch {
                    setLogs("任务已写入数据库，正在等待日志…");
                  }
                } catch (e: any) {
                  message.error(e.message);
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              {busy ? "正在处理" : runningElsewhere ? "已有任务运行中" : "开始处理"}
            </Button>
            {selected?.description && (
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                {selected.description}
              </Typography.Paragraph>
            )}
          </Space>
        </Card>
      </Col>

      <Col xs={24} xl={14}>
        <Card
          className="mosaic-panel"
          title={
            <Space>
              <span>处理进度</span>
              {job && <span className="mosaic-job-no">{formatNo(job.seq)}</span>}
              {job && <JobStatusTag status={job.status} />}
            </Space>
          }
          extra={
            job && (
              <JobActions
                job={job}
                inspect={inspect}
                size="middle"
                onChanged={async (next) => {
                  if (next) setJob(next);
                  else {
                    setJob(null);
                    setLogs("");
                    setSearchParams({});
                  }
                  await refresh();
                }}
              />
            )
          }
        >
          {!job ? (
            <Typography.Paragraph type="secondary">
              选择测区并开始一次正射处理，或从「处理记录」打开进行中的任务。
            </Typography.Paragraph>
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              {(jobState === "orphan" || jobState === "stale") && (
                <Alert
                  type="warning"
                  showIcon
                  message={jobState === "orphan" ? "包装进程已退出，进度不会再前进" : "库里显示运行中，Worker 没有在算"}
                  description="请点右上角「清理并续跑」。有 DSM 会从正射接着跑。不要用「杀死全部任务」。"
                />
              )}
              <Row gutter={12}>
                <Col span={8}>
                  <div className="mosaic-stat">
                    <div className="mosaic-stat-label">
                      <TitleWithProgressHelp>总进度</TitleWithProgressHelp>
                    </div>
                    <div className="mosaic-stat-value">{Math.round(job.global_percent || 0)}%</div>
                  </div>
                </Col>
                <Col span={8}>
                  <div className="mosaic-stat">
                    <div className="mosaic-stat-label">当前阶段</div>
                    <div className="mosaic-stat-value" style={{ fontSize: 15 }}>
                      {stageLabel(displayStage(job))}
                    </div>
                  </div>
                </Col>
                <Col span={8}>
                  <div className="mosaic-stat">
                    <div className="mosaic-stat-label">预计剩余</div>
                    <div className="mosaic-stat-value" style={{ fontSize: 15 }}>
                      {formatEta(job.eta_seconds)}
                    </div>
                  </div>
                </Col>
              </Row>

              <JobStageFlow job={job} />
              <Progress
                percent={Math.round(job.global_percent || 0)}
                status={
                  job.status === "failed"
                    ? "exception"
                    : job.status === "succeeded"
                      ? "success"
                      : job.status === "paused"
                        ? "normal"
                        : "active"
                }
              />
              {job.error_summary && <Typography.Text type="danger">{job.error_summary}</Typography.Text>}
              <JobSnapshot
                snapshot={job.params_snapshot}
                profileId={job.profile_id}
                profileVersion={job.profile_version}
              />
              <Collapse
                size="small"
                defaultActiveKey={["log"]}
                items={[
                  {
                    key: "log",
                    label: "处理日志",
                    children: <LogConsole text={logs} />,
                  },
                ]}
              />
            </Space>
          )}
        </Card>
      </Col>
    </Row>
  );
}
