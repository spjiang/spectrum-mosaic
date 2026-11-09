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
  Tooltip,
  Typography,
  message,
} from "antd";
import { PlayCircleOutlined, QuestionCircleOutlined } from "@ant-design/icons";
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
import ResourcePlan from "../components/ResourcePlan";
import { usableCores, type SceneFacts } from "../concurrencyPlan";
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

function FieldHelp({ label, help }: { label: string; help: string }) {
  return (
    <span className="title-with-help">
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Tooltip
        trigger={["hover", "click"]}
        placement="bottomLeft"
        title={<span style={{ display: "inline-block", maxWidth: 320 }}>{help}</span>}
      >
        <button
          type="button"
          className="param-help-btn"
          aria-label={`${label}说明`}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          <QuestionCircleOutlined />
        </button>
      </Tooltip>
    </span>
  );
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
  if (typeof raw === "number" && raw > 0) return Math.min(raw, max);
  return 0;
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

function pickGpu(raw: unknown): string {
  if (raw == null || raw === "") return "auto";
  const text = String(raw).trim().toLowerCase();
  if (["off", "none", "cpu", "false", "no"].includes(text)) return "off";
  if (/^\d+$/.test(text)) return String(Number(text));
  return "auto";
}

function jobGpu(job: any): string {
  return pickGpu(job?.gpu ?? job?.params_snapshot?.gpu);
}

function jobGpuMemoryGb(job: any): number {
  return asPositiveNumber(job?.gpu_memory_gb ?? job?.params_snapshot?.gpu_memory_gb);
}

type GpuDevice = { index: number; name: string; total_mb: number; free_mb: number; capability?: string };

function gpuSliderMax(devices: GpuDevice[]): number {
  const mb = Math.max(0, ...devices.map((d) => d.total_mb || 0));
  return mb > 0 ? Math.floor(mb / 1024) : 16;
}

export default function ExecutePage() {
  const { roles } = useAuth();
  const allowRun = canExecute(roles);
  const showOpsHelp = canViewOps(roles);
  const { jobs, refresh } = useJobs();
  const [searchParams, setSearchParams] = useSearchParams();
  const watchId = searchParams.get("job");
  const [profiles, setProfiles] = useState<any[]>([]);
  const [profileId, setProfileId] = useState<number | undefined>();
  const [inputDir, setInputDir] = useState("");
  const [nativeGsd, setNativeGsd] = useState<number | null>(null);
  const [scene, setScene] = useState<SceneFacts | null>(null);
  const [sceneLoading, setSceneLoading] = useState(false);
  const [gsdSamples, setGsdSamples] = useState(0);
  const [gsdNote, setGsdNote] = useState("");
  const [orthoScale, setOrthoScale] = useState(1);
  const [outputDir, setOutputDir] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [memoryGb, setMemoryGb] = useState<number | null>(0);
  const [engineRamGb, setEngineRamGb] = useState<number | null>(null);
  const [cpus, setCpus] = useState<number>(0);
  const [engineCpu, setEngineCpu] = useState<number | null>(null);
  const [gpu, setGpu] = useState<string>("auto");
  const [gpuMemoryGb, setGpuMemoryGb] = useState<number>(0);
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
            setGpu(pickGpu(preferred.values?.gpu ?? defaults.gpu));
            setGpuMemoryGb(asPositiveNumber(preferred.values?.gpu_memory_gb ?? defaults.gpu_memory_gb));
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
      if (p) {
        setGpu(pickGpu(p.values?.gpu));
        setGpuMemoryGb(asPositiveNumber(p.values?.gpu_memory_gb));
      }
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
        setGpu(jobGpu(j));
        setGpuMemoryGb(jobGpuMemoryGb(j));
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

  useEffect(() => {
    const dir = inputDir.trim();
    if (!dir.startsWith("/data")) {
      setNativeGsd(null);
      setScene(null);
      setSceneLoading(false);
      setGsdSamples(0);
      setGsdNote(dir ? "输入目录须位于 /data 下" : "");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSceneLoading(true);
      api
        .nativeGsd(dir)
        .then((r) => {
          if (cancelled) return;
          setNativeGsd(r.native_gsd_m);
          setGsdSamples(r.n_samples);
          setGsdNote("");
          if (r.width && r.height && r.n_color) {
            setScene({
              width: r.width,
              height: r.height,
              nColor: r.n_color,
              nMsBands: r.n_ms_bands ?? 0,
              nativeGsdM: r.native_gsd_m,
              spanXM: r.span_x_m ?? null,
              spanYM: r.span_y_m ?? null,
            });
          } else {
            setScene(null);
          }
        })
        .catch((e: any) => {
          if (cancelled) return;
          setNativeGsd(null);
          setScene(null);
          setGsdSamples(0);
          setGsdNote(e.message || "读不出原始分辨率");
        })
        .finally(() => {
          if (!cancelled) setSceneLoading(false);
        });
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [inputDir]);

  const summary = selected?.values || {};
  const busy = Boolean(job && ACTIVE.includes(job.status));
  const jobState = job ? jobComputeState(job, inspect) : "none";
  // 暂停和等待继续不再占机器。只有排队或正在计算才挡住新任务。
  const runningElsewhere = jobs.some(
    (j) => j.id !== job?.id && (j.status === "queued" || j.status === "running"),
  );
  const memMax = memorySliderMax(engineRamGb);
  const memMarks: Record<number, string> = { 0: "0" };
  if (memMax > 16) memMarks[16] = "16";
  if (memMax > 36) memMarks[36] = "36";
  memMarks[memMax] = String(memMax);
  const cpuMax = cpuSliderMax(engineCpu);
  const cpuMarks: Record<number, string> = { 0: "0" };
  if (cpuMax > 8) cpuMarks[8] = "8";
  cpuMarks[cpuMax] = String(cpuMax);
  const gpuInfo = inspect?.worker?.gpus;
  const gpuDevices: GpuDevice[] = Array.isArray(gpuInfo?.devices) ? gpuInfo.devices : [];
  const gpuReady = Boolean(gpuInfo?.available);
  const gpuMax = gpuSliderMax(gpuDevices);
  const gpuMarks: Record<number, string> = { 0: "0", [gpuMax]: String(gpuMax) };
  const gpuOptions = [
    { value: "auto", label: "自动（选空闲显存最多的卡）" },
    { value: "off", label: "不用 GPU" },
    ...gpuDevices.map((d) => ({
      value: String(d.index),
      label: `GPU ${d.index} ${d.name}（空闲 ${(d.free_mb / 1024).toFixed(1)} / ${(d.total_mb / 1024).toFixed(1)} GB）`,
    })),
  ];
  if (!gpuOptions.some((o) => o.value === gpu)) {
    gpuOptions.push({ value: gpu, label: `GPU ${gpu}（当前节点未检测到）` });
  }
  const gpuStatus = gpuReady
    ? `计算节点有 ${gpuDevices.length} 张可用显卡。匹配固定走 CPU。当前会用 GPU 的阶段：${
        (gpuInfo?.validated_stages || []).map((s: string) => stageLabel(s)).join("、") || "暂无，全部走 CPU"
      }。`
    : `计算节点${gpuInfo?.reason ? `：${gpuInfo.reason}` : "未上报显卡"}，任务会使用 CPU，结果相同。`;

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
              <FieldHelp
                label="标题"
                help="这条任务在处理记录里显示的名字。留空就用任务号和时间，例如 #36 2026-11-06 16:57。不影响计算。"
              />
              <Input
                style={{ marginTop: 6 }}
                disabled={!allowRun}
                maxLength={80}
                placeholder="留空则用任务号和时间，例如 #36 2026-11-06 16:57"
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
              />
            </div>
            <div>
              <FieldHelp
                label="处理方案"
                help="决定波段、格网和算法参数。全波段选「标准全波段」，只看彩色选「RGB 快速预览」。这里只选择，不要改方案内容。"
              />
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
                  setGpu(pickGpu(p?.values?.gpu));
                  setGpuMemoryGb(asPositiveNumber(p?.values?.gpu_memory_gb));
                }}
              />
            </div>
            <div>
              <FieldHelp
                label="测区数据"
                help="航片所在目录，必须是页面上的 /data/input/...，不要填自己电脑上的路径。S810 试用保持 1530 这一层。目录要对到架次，只丢一张照片认不出来。"
              />
              <Input
                style={{ marginTop: 6 }}
                disabled={!allowRun}
                placeholder="选择已挂载的测区目录"
                value={inputDir}
                onChange={(e) => setInputDir(e.target.value)}
              />
              <div style={{ marginTop: 10 }}>
                <span className="title-with-help">
                  <Typography.Text type="secondary">降低分辨率</Typography.Text>
                  <Tooltip
                    title={
                      nativeGsd
                        ? `像元越小，分辨率越高。当前原始像元 ${nativeGsd.toFixed(4)} m，比降低后的 ${(nativeGsd * 4).toFixed(4)} m 更细。选「降低到 1/2」或「降低到 1/4」是把像元放大，分辨率变低，正射更快。`
                        : "像元越小，分辨率越高。数字越小，一个像元盖住的地面越小、看得越细。选「降低到 1/2」或「降低到 1/4」是把像元放大，分辨率变低，正射更快。"
                    }
                  >
                    <button type="button" className="param-help-btn" aria-label="降低分辨率说明">
                      <QuestionCircleOutlined />
                    </button>
                  </Tooltip>
                </span>
                {summary.grid_reference ? (
                  <div style={{ marginTop: 6 }}>
                    <Typography.Text>已锁定交付格网，不按这里的倍数改分辨率。</Typography.Text>
                    {nativeGsd ? (
                      <Typography.Text type="secondary">
                        {" "}
                        影像原始分辨率 {nativeGsd.toFixed(4)} m（航高取 {gsdSamples} 张 Color）
                      </Typography.Text>
                    ) : null}
                  </div>
                ) : (
                  <>
                    <Select
                      style={{ width: "100%", marginTop: 6 }}
                      disabled={!allowRun || nativeGsd == null}
                      value={orthoScale}
                      options={[1, 2, 4].map((scale) => ({
                        value: scale,
                        label: nativeGsd
                          ? scale === 1
                            ? `不降低　${nativeGsd.toFixed(4)} m`
                            : `降低到 1/${scale}　${(nativeGsd * scale).toFixed(4)} m`
                          : scale === 1
                            ? "不降低"
                            : `降低到 1/${scale}`,
                      }))}
                      onChange={(v) => setOrthoScale(v)}
                    />
                    <div style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary">
                        {nativeGsd
                          ? `原始分辨率 ${nativeGsd.toFixed(4)} m（航高取 ${gsdSamples} 张 Color）。选 1/2、1/4 会把像元放大，正射更快。`
                          : gsdNote || "填写测区目录后自动读取原始分辨率"}
                      </Typography.Text>
                    </div>
                  </>
                )}
              </div>
            </div>
            <div>
              <FieldHelp
                label="成果目录"
                help="成果写入的根目录，填写 /data/output/runs/任务名。每次开始处理会在下面新建时间戳子目录，不覆盖上一次成果。"
              />
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
                <FieldHelp
                  label="任务内存"
                  help="这次计算最多占用的内存。滑到 0 表示按机器可用内存自动封顶，不会超过节点上限。试用保持方案带出的值。"
                />
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
              <Typography.Paragraph type="secondary" style={{ margin: "0 0 0", fontSize: 12, lineHeight: 1.6 }}>
                0 表示按节点可见内存自动封顶，配得再高也不会超过这个上限。
                {showOpsHelp
                  ? `当前节点 ${engineRamGb != null ? `${engineRamGb} GB` : "内存未知"}。`
                  : ""}
              </Typography.Paragraph>
            </div>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <FieldHelp
                  label="任务 CPU"
                  help="这次计算最多用几个核，也是同时开的进程上限。滑到 0 表示自动，并留 2 核给系统和页面。拉满容易把页面卡住。试用保持方案带出的值。"
                />
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
              <Typography.Paragraph type="secondary" style={{ margin: "0 0 0", fontSize: 12, lineHeight: 1.6 }}>
                0 表示自动，留 2 核给系统和页面。核数是进程数的上限。
                {showOpsHelp ? `当前节点 ${engineCpu != null ? `${engineCpu} 核` : "核数未知"}。` : ""}
                {engineCpu != null && engineCpu > 4 ? `拉到 ${engineCpu} 会占满本机，系统和页面容易卡住。` : ""}
              </Typography.Paragraph>
            </div>
            {sceneLoading ? (
              <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0", fontSize: 12 }}>
                正在读取测区影像和航线范围。
              </Typography.Paragraph>
            ) : scene == null ? (
              <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0", fontSize: 12 }}>
                选定测区后，按影像尺寸、波段和航线范围估算各阶段能开几路。
              </Typography.Paragraph>
            ) : (
              (() => {
                const planMemory = memoryGb && memoryGb > 0 ? memoryGb : engineRamGb;
                const planCpu = cpus > 0 ? cpus : usableCores(engineCpu) ?? engineCpu;
                if (planMemory == null || planCpu == null || planMemory <= 0 || planCpu <= 0) {
                  return (
                    <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0", fontSize: 12 }}>
                      测区已读到。等计算节点的内存和核数到齐后给出并发。
                    </Typography.Paragraph>
                  );
                }
                return (
                  <ResourcePlan
                    scene={scene}
                    memoryGb={planMemory}
                    cpus={planCpu}
                    orthoScale={orthoScale}
                    memoryCeilingGb={engineRamGb}
                    cpuCeiling={engineCpu}
                    disabled={!allowRun}
                    onApply={(gb, n) => {
                      setMemoryGb(Math.min(gb, memMax));
                      setCpus(Math.min(n, cpuMax));
                    }}
                  />
                );
              })()
            )}
            <div>
              <FieldHelp
                label="任务 GPU"
                help="选择用哪张显卡。像对匹配固定走 CPU，选了显卡也不表示每一步都用 GPU。没有可用显卡时自动改用 CPU，结果相同。试用保持方案带出的值。"
              />
              <Select
                style={{ width: "100%", marginTop: 6 }}
                disabled={!allowRun}
                value={gpu}
                options={gpuOptions}
                onChange={(v) => setGpu(v)}
              />
              <Typography.Paragraph type="secondary" style={{ margin: "6px 0 0", fontSize: 12, lineHeight: 1.55 }}>
                {gpuStatus}
              </Typography.Paragraph>
            </div>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <FieldHelp
                  label="任务显存"
                  help="这次允许占用的显存。滑到 0 表示用所选显卡当前空闲显存的 85%。不够会自动分批，仍不够则该阶段改用 CPU。选「不用 GPU」时这项不生效。"
                />
                <Typography.Text strong>
                  {gpu === "off" ? "不使用" : gpuMemoryGb ? `${gpuMemoryGb} GB` : "自动"}
                </Typography.Text>
              </div>
              <div className="memory-slider">
                <Slider
                  min={0}
                  max={gpuMax}
                  step={0.5}
                  disabled={!allowRun || gpu === "off"}
                  value={Math.min(gpuMemoryGb, gpuMax)}
                  onChange={(v) => setGpuMemoryGb(typeof v === "number" ? v : 0)}
                  marks={gpuMarks}
                  tooltip={{ formatter: (v) => (v ? `${v} GB` : "自动") }}
                />
              </div>
              <Typography.Paragraph type="secondary" style={{ margin: "0 0 0", fontSize: 12, lineHeight: 1.55 }}>
                0 表示用所选显卡当前空闲显存的 85%。配得再高也不会超过空闲显存；显存不够时自动分批，仍不够则该阶段改用 CPU。
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
                      <Descriptions.Item label="特征点上限">{summary.max_features ?? 8192}</Descriptions.Item>
                      <Descriptions.Item label="memory_gb">{String(summary.memory_gb ?? 0)}</Descriptions.Item>
                      <Descriptions.Item label="cpus">{String(summary.cpus ?? 0)}</Descriptions.Item>
                      <Descriptions.Item label="gpu">{String(summary.gpu ?? "auto")}</Descriptions.Item>
                      <Descriptions.Item label="gpu_memory_gb">{String(summary.gpu_memory_gb ?? 0)}</Descriptions.Item>
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
                    title: jobTitle.trim(),
                    input_dir: inputDir,
                    output_dir: outputDir,
                    params: {
                      memory_gb: memoryGb ?? 0,
                      cpus: cpus ?? 0,
                      gpu,
                      gpu_memory_gb: gpu === "off" ? 0 : gpuMemoryGb,
                      ...(nativeGsd != null && !summary.grid_reference
                        ? {
                            ortho_scale: orthoScale,
                            native_gsd_m: nativeGsd,
                            ortho_gsd_m: nativeGsd * orthoScale,
                            dsm_gsd: nativeGsd * orthoScale * 2,
                          }
                        : {}),
                    },
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
