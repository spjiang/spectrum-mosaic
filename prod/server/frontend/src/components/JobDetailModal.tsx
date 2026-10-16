import { useCallback, useEffect, useState } from "react";
import { Button, Descriptions, Modal, Progress, Space, Typography, message } from "antd";
import { DownloadOutlined, FilePdfOutlined } from "@ant-design/icons";
import { api } from "../api";
import { formatNo } from "../ids";
import { attachJobProcesses } from "../processTree";
import JobActions from "./JobActions";
import JobProcessTable from "./JobProcessTable";
import JobSnapshot from "./JobSnapshot";
import JobStageFlow from "./JobStageFlow";
import JobStatusTag from "./JobStatusTag";
import { formatJobDateTime, jobElapsedHours } from "../jobTime";
import LogConsole from "./LogConsole";

const LIVE = ["queued", "running", "paused", "awaiting_continue"];

export default function JobDetailModal({
  open,
  jobId,
  inspect,
  onClose,
  onChanged,
  onDownloadParams,
}: {
  open: boolean;
  jobId?: string;
  inspect?: any;
  onClose: () => void;
  onChanged?: () => void | Promise<void>;
  onDownloadParams: (job: any) => void;
}) {
  const [job, setJob] = useState<any>(null);
  const [logs, setLogs] = useState("");

  const load = useCallback(async (id: string) => {
    const [detail, nextLogs] = await Promise.all([api.job(id), api.logs(id)]);
    setJob(detail);
    setLogs(nextLogs);
  }, []);

  useEffect(() => {
    if (!open || !jobId) {
      setJob(null);
      setLogs("");
      return;
    }
    let cancelled = false;
    load(jobId).catch((e: any) => {
      if (!cancelled) message.error(e.message || String(e));
    });
    return () => {
      cancelled = true;
    };
  }, [open, jobId, load]);

  useEffect(() => {
    if (!open || !job?.id || !LIVE.includes(job.status)) return;
    const id = job.id;
    const t = setInterval(() => {
      load(id).catch(() => undefined);
    }, 1000);
    return () => clearInterval(t);
  }, [open, job?.id, job?.status, load]);

  const percent = Math.round(job?.global_percent || 0);
  const processes = job ? attachJobProcesses(job, inspect).processes || [] : [];

  return (
    <Modal
      className="job-detail-modal"
      open={open}
      onCancel={onClose}
      width="92vw"
      centered
      destroyOnClose
      styles={{ body: { maxHeight: "calc(100vh - 160px)", overflow: "auto" } }}
      title={
        job ? (
          <Space size={10} wrap>
            <span>任务 {formatNo(job.seq)}</span>
            <JobStatusTag status={job.status} />
            <Progress
              percent={percent}
              size="small"
              status={
                job.status === "failed" ? "exception" : job.status === "succeeded" ? "success" : "active"
              }
              style={{ width: 160, marginBottom: 0 }}
            />
          </Space>
        ) : (
          "任务详情"
        )
      }
      footer={
        job ? (
          <div className="job-detail-footer">
            <JobActions
              job={job}
              inspect={inspect}
              size="middle"
              onChanged={async (next) => {
                if (!next) {
                  onClose();
                  await onChanged?.();
                  return;
                }
                setJob(next);
                const nextLogs = await api.logs(next.id).catch(() => logs);
                setLogs(nextLogs);
                await onChanged?.();
              }}
            />
            <Space wrap>
              <Button icon={<DownloadOutlined />} onClick={() => onDownloadParams(job)}>
                下载参数
              </Button>
              <Button
                icon={<FilePdfOutlined />}
                onClick={async () => {
                  try {
                    await api.openJobReport(job.id);
                  } catch (e: any) {
                    message.error(e.message);
                  }
                }}
              >
                质量报告
              </Button>
              <Button type="primary" onClick={onClose}>
                关闭
              </Button>
            </Space>
          </div>
        ) : null
      }
    >
      {!job ? (
        <Typography.Text type="secondary">正在加载…</Typography.Text>
      ) : (
        <div className="job-detail-body">
          {job.message ? <div className="job-detail-message">{job.message}</div> : null}
          {job.error_summary ? <Typography.Text type="danger">{job.error_summary}</Typography.Text> : null}

          <section className="job-detail-section">
            <div className="job-detail-section-title">流程</div>
            <JobStageFlow job={job} />
          </section>

          <section className="job-detail-section">
            <div className="job-detail-section-title">基本信息</div>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="任务内存">
                {job.memory_gb ? `${job.memory_gb} GB` : "自动（按容器空闲内存）"}
              </Descriptions.Item>
              <Descriptions.Item label="任务 CPU">
                {job.cpus ? `${job.cpus} 核` : "自动（按引擎 CPU）"}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatJobDateTime(job.created_at)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{formatJobDateTime(job.finished_at)}</Descriptions.Item>
              <Descriptions.Item label="总耗时">{jobElapsedHours(job)}</Descriptions.Item>
              <Descriptions.Item label="输入" span={2}>
                <Typography.Text copyable={{ text: job.input_dir }} className="job-detail-path">
                  {job.input_dir || "—"}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="输出" span={2}>
                <Typography.Text copyable={{ text: job.output_dir }} className="job-detail-path">
                  {job.output_dir || "—"}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="内部 UUID" span={2}>
                <Typography.Text copyable={{ text: String(job.id) }}>{job.id}</Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          </section>

          <section className="job-detail-section">
            <div className="job-detail-section-title">Worker 进程</div>
            <JobProcessTable processes={processes} full />
          </section>

          <JobSnapshot
            snapshot={job.params_snapshot}
            profileId={job.profile_id}
            profileVersion={job.profile_version}
          />

          <section className="job-detail-section">
            <div className="job-detail-section-title">运行日志</div>
            <LogConsole text={logs} empty="（无日志）" />
          </section>
        </div>
      )}
    </Modal>
  );
}
