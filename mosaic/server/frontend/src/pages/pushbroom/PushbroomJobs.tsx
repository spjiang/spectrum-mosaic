import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { DownloadOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { api, type PushbroomFile, type PushbroomJob, type PushbroomStatus } from "../../api";
import { formatNo } from "../../ids";
import { formatJobDateTime } from "../../jobTime";
import { useAuth } from "../../auth";
import {
  PB_ACTIVE,
  PB_STAGES,
  PB_STATUS,
  flightSize,
  formatBandList,
  formatBytes,
  formatDuration,
  stageLabel,
} from "./labels";

const POLL_MS = 3000;

function elapsedSeconds(job: PushbroomJob): number | null {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return (end - Date.parse(job.started_at)) / 1000;
}

function StatusTag({ job }: { job: PushbroomJob }) {
  const s = PB_STATUS[job.status] ?? { text: job.status, color: "default" };
  return <Tag color={s.color}>{s.text}</Tag>;
}

function JobProgress({ job }: { job: PushbroomJob }) {
  if (job.status === "queued") return <Typography.Text type="secondary">等待计算服务</Typography.Text>;
  const status = job.status === "failed" ? "exception" : job.status === "succeeded" ? "success" : "active";
  return (
    <div style={{ minWidth: 160 }}>
      <Progress percent={Math.round(job.global_percent || 0)} size="small" status={status} />
      {PB_ACTIVE.includes(job.status) && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {stageLabel(job.current_stage)}
          {job.message ? ` · ${job.message}` : ""}
        </Typography.Text>
      )}
    </div>
  );
}

function JobDrawer({
  jobId,
  admin,
  onClose,
  onChanged,
}: {
  jobId: string | null;
  admin: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [job, setJob] = useState<PushbroomJob | null>(null);
  const [files, setFiles] = useState<PushbroomFile[]>([]);
  const [logs, setLogs] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  const load = useCallback(async () => {
    if (!jobId) return;
    const [j, f, l] = await Promise.all([api.pushbroomJob(jobId), api.pushbroomFiles(jobId), api.pushbroomLogs(jobId)]);
    setJob(j);
    setFiles(f.items);
    setLogs(l);
  }, [jobId]);

  useEffect(() => {
    setJob(null);
    setFiles([]);
    setLogs("");
    if (!jobId) return;
    load().catch((err) => message.error((err as Error).message || String(err)));
  }, [jobId, load]);

  const active = job ? PB_ACTIVE.includes(job.status) : false;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => {
      load()
        .then(onChanged)
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [active, load, onChanged]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  async function download(f: PushbroomFile) {
    if (!job) return;
    try {
      await api.pushbroomDownload(job.id, f.path);
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  return (
    <Drawer
      open={Boolean(jobId)}
      onClose={onClose}
      width={760}
      title={job ? `推扫任务 ${formatNo(job.seq)} · ${job.title}` : "推扫任务"}
      destroyOnClose
    >
      {job && (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {job.error_summary && <Alert type="error" showIcon message="失败原因" description={job.error_summary} />}
          {job.calibration_warning && (
            <Alert
              type="warning"
              showIcon
              message="本架次与当前标定不符，航带接缝可能错开。"
              description={admin ? <Link to="/pushbroom/calibration">去标定</Link> : job.calibration_warning}
            />
          )}
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="状态">
              <StatusTag job={job} />
              {job.run_attempt > 1 && <Typography.Text type="secondary">第 {job.run_attempt} 次运行</Typography.Text>}
            </Descriptions.Item>
            <Descriptions.Item label="进度">
              <JobProgress job={job} />
            </Descriptions.Item>
            <Descriptions.Item label="架次目录" span={2}>
              <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                {job.input_dir}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="成果目录" span={2}>
              <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                {job.output_dir}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="影像尺寸">{flightSize(job.flight)}</Descriptions.Item>
            <Descriptions.Item label="原始数据">{formatBytes(job.flight.spe_bytes)}</Descriptions.Item>
            <Descriptions.Item label="分辨率">
              {job.params.gsd_m ? `${(job.params.gsd_m * 100).toFixed(1)} cm` : "自动"}
            </Descriptions.Item>
            <Descriptions.Item label="输出波段">{formatBandList(job.params.bands)}</Descriptions.Item>
            <Descriptions.Item label="沿航向偏移">
              {job.params.along_offset_m != null ? `${job.params.along_offset_m.toFixed(2)} m（本任务标定）` : "内置"}
            </Descriptions.Item>
            <Descriptions.Item label="狭缝中心偏置">
              {job.params.boresight_px != null ? `${job.params.boresight_px.toFixed(1)} 像元（本任务标定）` : "内置"}
            </Descriptions.Item>
            <Descriptions.Item label="提交时间">{formatJobDateTime(job.created_at)}</Descriptions.Item>
            <Descriptions.Item label="耗时">{formatDuration(elapsedSeconds(job))}</Descriptions.Item>
          </Descriptions>

          <Card size="small" title="各步耗时">
            <Space wrap>
              {PB_STAGES.map((s) => {
                const sec = job.stage_timings[s.id];
                const current = job.current_stage === s.id && PB_ACTIVE.includes(job.status);
                return (
                  <Tag key={s.id} color={sec != null ? "success" : current ? "processing" : "default"}>
                    {s.label}
                    {sec != null ? ` ${formatDuration(sec)}` : current ? " 进行中" : ""}
                  </Tag>
                );
              })}
            </Space>
          </Card>

          <Card size="small" title="成果文件">
            <Table
              rowKey="path"
              size="small"
              dataSource={files}
              pagination={false}
              locale={{ emptyText: job.status === "succeeded" ? "成果目录为空" : "完成后在这里下载" }}
              columns={[
                { title: "文件", dataIndex: "name", ellipsis: true },
                { title: "类别", dataIndex: "kind", width: 90, render: (k: string) => (k === "report" ? "报告" : "成果") },
                { title: "大小", dataIndex: "size", width: 100, render: (n: number) => formatBytes(n) },
                {
                  title: "",
                  width: 90,
                  render: (_: unknown, f: PushbroomFile) => (
                    <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => download(f)}>
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          </Card>

          <Card size="small" title="运行日志">
            <pre
              ref={logRef}
              style={{ maxHeight: 320, overflow: "auto", margin: 0, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}
            >
              {logs}
            </pre>
          </Card>
        </Space>
      )}
    </Drawer>
  );
}

export default function PushbroomJobsPage() {
  const nav = useNavigate();
  const { roles } = useAuth();
  const admin = roles.includes("admin");
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<PushbroomJob[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [svc, setSvc] = useState<PushbroomStatus | null>(null);
  const openId = params.get("job");

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const [data, st] = await Promise.all([api.pushbroomJobs(page, pageSize, statusFilter), api.pushbroomStatus()]);
        setRows(data.items);
        setTotal(data.total);
        setSvc(st);
      } catch (err) {
        if (!quiet) message.error((err as Error).message || String(err));
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [page, pageSize, statusFilter],
  );

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const anyActive = rows.some((r) => PB_ACTIVE.includes(r.status));
  useEffect(() => {
    if (!anyActive) return;
    const t = window.setInterval(() => load(true).catch(() => undefined), POLL_MS);
    return () => window.clearInterval(t);
  }, [anyActive, load]);

  const quietReload = useCallback(() => {
    load(true).catch(() => undefined);
  }, [load]);

  function openJob(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("job", id);
    else next.delete("job");
    setParams(next, { replace: true });
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      message.success(ok);
      await load(true);
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  function confirmCancel(job: PushbroomJob) {
    Modal.confirm({
      title: `取消推扫任务 ${formatNo(job.seq)}？`,
      content: job.status === "running" ? "正在计算的进程会被停止，已写出的中间文件保留在成果目录。" : "任务还在排队，取消后不会开始计算。",
      okText: "取消任务",
      okButtonProps: { danger: true },
      cancelText: "返回",
      onOk: () => act(() => api.pushbroomCancel(job.id), "已发出取消"),
    });
  }

  function confirmDelete(job: PushbroomJob) {
    let withFiles = false;
    Modal.confirm({
      title: `删除推扫任务 ${formatNo(job.seq)}？`,
      content: (
        <Space direction="vertical">
          <span>删除任务记录和日志。</span>
          <Checkbox onChange={(e) => (withFiles = e.target.checked)}>
            同时删除成果目录 <Typography.Text code>{job.output_dir}</Typography.Text>
          </Checkbox>
        </Space>
      ),
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "返回",
      onOk: () => act(() => api.pushbroomDelete(job.id, withFiles), withFiles ? "任务和成果已删除" : "任务记录已删除，成果文件保留"),
    });
  }

  return (
    <Card
      className="mosaic-panel"
      title="推扫记录"
      extra={
        <Space>
          {svc && (
            <Tag color={svc.worker_online ? "success" : svc.worker_online === false ? "warning" : "default"}>
              推扫计算服务{svc.worker_online ? "在线" : svc.worker_online === false ? "未在线" : "状态未知"}
            </Tag>
          )}
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 130 }}
            value={statusFilter}
            onChange={(v) => {
              setStatusFilter(v);
              setPage(1);
            }}
            options={Object.entries(PB_STATUS).map(([value, s]) => ({ value, label: s.text }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => load()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => nav("/pushbroom/new")}>
            新建推扫
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        tableLayout="fixed"
        scroll={{ x: admin ? 1240 : 1130 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (n) => `共 ${n} 条`,
          onChange: (p, s) => {
            setPage(p);
            setPageSize(s);
          },
        }}
        columns={[
          { title: "编号", dataIndex: "seq", width: 80, render: (n: number) => formatNo(n) },
          {
            title: "任务",
            dataIndex: "title",
            width: 220,
            ellipsis: true,
            render: (_: unknown, r: PushbroomJob) => (
              <Button type="link" style={{ padding: 0 }} onClick={() => openJob(r.id)}>
                {r.title}
              </Button>
            ),
          },
          { title: "状态", width: 100, render: (_: unknown, r: PushbroomJob) => <StatusTag job={r} /> },
          { title: "进度", width: 240, render: (_: unknown, r: PushbroomJob) => <JobProgress job={r} /> },
          ...(admin ? [{ title: "提交人", dataIndex: "created_by", width: 110, render: (v: string | null) => v || "—" }] : []),
          { title: "提交时间", dataIndex: "created_at", width: 170, render: (v: string | null) => formatJobDateTime(v) },
          { title: "耗时", width: 120, render: (_: unknown, r: PushbroomJob) => formatDuration(elapsedSeconds(r)) },
          {
            title: "操作",
            width: 180,
            fixed: "right" as const,
            render: (_: unknown, r: PushbroomJob) => (
              <Space size={0}>
                <Button type="link" onClick={() => openJob(r.id)}>
                  详情
                </Button>
                {PB_ACTIVE.includes(r.status) && r.status !== "cancelling" && (
                  <Button type="link" danger onClick={() => confirmCancel(r)}>
                    取消
                  </Button>
                )}
                {(r.status === "failed" || r.status === "cancelled") && (
                  <Button type="link" onClick={() => act(() => api.pushbroomRetry(r.id), "已重新提交")}>
                    重跑
                  </Button>
                )}
                {!PB_ACTIVE.includes(r.status) && (
                  <Button type="link" danger onClick={() => confirmDelete(r)}>
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <JobDrawer jobId={openId} admin={admin} onClose={() => openJob(null)} onChanged={quietReload} />
    </Card>
  );
}
