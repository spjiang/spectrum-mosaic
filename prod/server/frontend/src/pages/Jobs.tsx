import { useEffect, useMemo, useState } from "react";
import { Button, Card, Input, InputNumber, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { DeleteOutlined, DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useJobs } from "../jobsContext";
import { formatNo } from "../ids";
import { STAGE_META, STAGES } from "../stages";
import JobStatusTag from "../components/JobStatusTag";
import JobActions from "../components/JobActions";
import JobStageFlow from "../components/JobStageFlow";
import JobProcessTable from "../components/JobProcessTable";
import JobDetailModal from "../components/JobDetailModal";
import { ResizableHeader, useColumnWidths } from "../components/ResizableHeader";
import { attachJobProcesses, fmtMb, jobComputeState } from "../processTree";
import { downloadJson, jobParamsFilename, serializeJobParams } from "../profileExport";
import { TitleWithProgressHelp } from "../components/ProgressHelpTip";
import { formatJobDateTime, jobElapsedHours } from "../jobTime";
import { useAuth } from "../auth";
import { canExecute } from "../roles";

const STATUS_OPTIONS = [
  { value: "queued", label: "排队" },
  { value: "running", label: "运行中" },
  { value: "paused", label: "已暂停" },
  { value: "awaiting_continue", label: "等待继续" },
  { value: "succeeded", label: "成功" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

const STAGE_OPTIONS = STAGES.map((s) => ({ value: s, label: `${STAGE_META[s].label}（${s}）` }));

const LIVE = ["queued", "running", "paused", "awaiting_continue"];

const COL_W = {
  seq: 92,
  status: 110,
  progress: 96,
  stages: 220,
  procs: 150,
  memory: 96,
  cpus: 72,
  created: 170,
  finished: 170,
  elapsed: 110,
  input: 220,
  output: 240,
  actions: 430,
};

export default function JobsPage() {
  const { jobs: rows, loading, refresh } = useJobs();
  const { roles } = useAuth();
  const allowRun = canExecute(roles);
  const [profiles, setProfiles] = useState<any[]>([]);
  const [seq, setSeq] = useState<number | null>(null);
  const [status, setStatus] = useState<string | undefined>();
  const [stage, setStage] = useState<string | undefined>();
  const [profileId, setProfileId] = useState<number | undefined>();
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [keyword, setKeyword] = useState("");
  const [inspect, setInspect] = useState<any>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [detailId, setDetailId] = useState<string | undefined>();
  const nav = useNavigate();
  const { widths, setWidth, total } = useColumnWidths("jobs-table-v4", COL_W);

  async function reload() {
    await refresh();
  }

  async function downloadParams(job: any) {
    try {
      const detail = job?.params_snapshot ? job : await api.job(job.id);
      const snap = detail.params_snapshot;
      if (!snap || typeof snap !== "object" || !Object.keys(snap).length) {
        message.warning("该任务没有保存参数快照");
        return;
      }
      downloadJson(jobParamsFilename(detail), serializeJobParams(detail));
      message.success("已下载该任务使用的参数，可在「处理方案」里导入");
    } catch (e: any) {
      message.error(e.message || String(e));
    }
  }

  useEffect(() => {
    api
      .profiles()
      .then((list) => setProfiles(Array.isArray(list) ? list : []))
      .catch(() => setProfiles([]));
  }, []);

  useEffect(() => {
    let stopped = false;
    let busy = false;
    const tick = async () => {
      if (stopped || busy || document.hidden) return;
      if (!rows.some((r) => LIVE.includes(r.status))) {
        setInspect(null);
        return;
      }
      busy = true;
      try {
        const next = await api.workerInspect();
        if (!stopped) setInspect(next);
      } catch {
        /* 进程信息失败不影响任务列表 */
      } finally {
        busy = false;
      }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [rows]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (seq != null && Number(r.seq) !== seq) return false;
      if (status && r.status !== status) return false;
      if (stage && r.current_stage !== stage && r.completed_stage !== stage) return false;
      if (profileId != null && r.profile_id !== profileId) return false;
      if (fromDate) {
        const t = String(r.created_at || "").slice(0, 10);
        if (t && t < fromDate) return false;
      }
      if (toDate) {
        const t = String(r.created_at || "").slice(0, 10);
        if (t && t > toDate) return false;
      }
      if (keyword) {
        const k = keyword.toLowerCase();
        const blob = `${r.seq ?? ""} ${r.id} ${r.input_dir} ${r.output_dir} ${r.message || ""} ${r.current_stage || ""}`.toLowerCase();
        if (!blob.includes(k)) return false;
      }
      return true;
    });
  }, [rows, seq, status, stage, profileId, fromDate, toDate, keyword]);

  const dataSource = useMemo(
    () =>
      filtered.map((r) => ({
        ...r,
        ...attachJobProcesses(r, inspect),
        compute_state: jobComputeState(r, inspect),
      })),
    [filtered, inspect],
  );

  const col = (key: keyof typeof COL_W, spec: Record<string, unknown>) => ({
    ...spec,
    width: widths[key],
    onHeaderCell: () => ({ width: widths[key], onResize: (w: number) => setWidth(key, w) }),
  });

  return (
    <>
      <Card
        className="mosaic-panel"
        title="处理记录"
        extra={
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => reload()}>
              刷新
            </Button>
            {allowRun && (
            <Popconfirm
              title="清空全部处理记录？"
              description="将停止正在计算的任务，并从列表删除全部记录。成果目录保留，此操作不可撤销。"
              okText="全部清空"
              okButtonProps={{ danger: true }}
              disabled={rows.length === 0}
              onConfirm={async () => {
                try {
                  const r = await api.clearJobs();
                  message.success(`已清空 ${r.deleted} 条任务`);
                  setDetailId(undefined);
                  await reload();
                } catch (e: any) {
                  message.error(e.message);
                }
              }}
            >
              <Button danger icon={<DeleteOutlined />} disabled={rows.length === 0}>
                清空全部
              </Button>
            </Popconfirm>
            )}
          </Space>
        }
      >
        <div className="mosaic-filter-bar">
          <InputNumber
            min={1}
            placeholder="任务号"
            value={seq}
            onChange={(v) => setSeq(typeof v === "number" ? v : null)}
            style={{ width: 110 }}
          />
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 140 }}
            value={status}
            onChange={setStatus}
            options={STATUS_OPTIONS}
          />
          <Select
            allowClear
            placeholder="阶段"
            style={{ width: 180 }}
            value={stage}
            onChange={setStage}
            options={STAGE_OPTIONS}
          />
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="处理方案"
            style={{ width: 220 }}
            value={profileId}
            onChange={setProfileId}
            options={profiles.map((p) => ({ value: p.id, label: `${formatNo(p.id)} ${p.name}` }))}
          />
          <Input type="date" style={{ width: 150 }} value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          <Input type="date" style={{ width: 150 }} value={toDate} onChange={(e) => setToDate(e.target.value)} />
          <Input.Search
            allowClear
            placeholder="路径 / 说明 / UUID"
            style={{ width: 240 }}
            onSearch={setKeyword}
            onChange={(e) => !e.target.value && setKeyword("")}
          />
        </div>
        <Table
          rowKey="id"
          dataSource={dataSource}
          pagination={{ pageSize: 12, showSizeChanger: false, showTotal: (n) => `共 ${n} 条` }}
          scroll={{ x: total }}
          tableLayout="fixed"
          components={{ header: { cell: ResizableHeader } }}
          expandable={{
            expandedRowKeys: expanded,
            onExpandedRowsChange: (keys) => setExpanded(keys.map(String)),
            rowExpandable: (r) => (r.processes || []).length > 0,
            expandedRowRender: (r) => <JobProcessTable processes={r.processes || []} />,
          }}
          columns={[
            col("seq", {
              title: "任务号",
              dataIndex: "seq",
              render: (v: number) => <Typography.Text strong>{formatNo(v)}</Typography.Text>,
            }),
            col("status", {
              title: "状态",
              dataIndex: "status",
              render: (v: string, r: any) => (
                <Space size={4} wrap>
                  <JobStatusTag status={v} />
                  {r.compute_state === "orphan" ? <Tag color="orange">进程脱离</Tag> : null}
                  {r.compute_state === "stale" ? <Tag>未在计算</Tag> : null}
                </Space>
              ),
            }),
            col("progress", {
              title: <TitleWithProgressHelp>进度</TitleWithProgressHelp>,
              dataIndex: "global_percent",
              render: (v: number) => `${Math.round(v || 0)}%`,
            }),
            col("stages", {
              title: "流程",
              render: (_: unknown, r: any) => <JobStageFlow job={r} variant="compact" />,
            }),
            col("procs", {
              title: "进程",
              dataIndex: "process_count",
              render: (v: number, r: any) => (v ? `${v} 个 / ${fmtMb(r.process_rss_mb)}` : "—"),
            }),
            col("memory", {
              title: "内存",
              dataIndex: "memory_gb",
              render: (v: number) => (v ? `${v} GB` : "自动"),
            }),
            col("cpus", {
              title: "CPU",
              dataIndex: "cpus",
              render: (v: number) => (v ? `${v} 核` : "自动"),
            }),
            col("created", {
              title: "创建时间",
              dataIndex: "created_at",
              render: (v: string) => formatJobDateTime(v),
            }),
            col("finished", {
              title: "结束时间",
              dataIndex: "finished_at",
              render: (v: string) => formatJobDateTime(v),
            }),
            col("elapsed", {
              title: "总耗时",
              render: (_: unknown, r: any) => jobElapsedHours(r),
            }),
            col("input", {
              title: "输入",
              dataIndex: "input_dir",
              ellipsis: true,
              render: (v: string) => <Typography.Text copyable={{ text: v }}>{v}</Typography.Text>,
            }),
            col("output", {
              title: "输出",
              dataIndex: "output_dir",
              ellipsis: true,
              render: (v: string) => <Typography.Text copyable={{ text: v }}>{v}</Typography.Text>,
            }),
            col("actions", {
              title: "操作",
              render: (_: unknown, r: any) => (
                <Space wrap size={4}>
                  <Button size="small" type="link" onClick={() => setDetailId(r.id)}>
                    详情
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    icon={<DownloadOutlined />}
                    onClick={async () => {
                      try {
                        await api.openJobReport(r.id);
                      } catch (e: any) {
                        message.error(e.message);
                      }
                    }}
                  >
                    报告
                  </Button>
                  <Button size="small" type="link" icon={<DownloadOutlined />} onClick={() => downloadParams(r)}>
                    参数
                  </Button>
                  <Button size="small" type="link" onClick={() => nav(`/execute?job=${r.id}`)}>
                    {LIVE.includes(r.status) ? "监控" : "查看"}
                  </Button>
                  <JobActions job={r} inspect={inspect} computeState={r.compute_state} onChanged={() => reload()} />
                </Space>
              ),
            }),
          ]}
        />
      </Card>

      <JobDetailModal
        open={Boolean(detailId)}
        jobId={detailId}
        inspect={inspect}
        onClose={() => setDetailId(undefined)}
        onChanged={reload}
        onDownloadParams={downloadParams}
      />
    </>
  );
}
