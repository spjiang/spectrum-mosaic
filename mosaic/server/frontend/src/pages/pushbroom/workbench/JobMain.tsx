import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Empty, Flex, Progress, Spin, Steps, Typography, message } from "antd";
import { DownloadOutlined, ExpandOutlined } from "@ant-design/icons";
import { api, type PushbroomJob } from "../../../api";
import { ImageInspectModal } from "../../../components/ImageStage";
import { formatNo } from "../../../ids";
import { PB_STAGES, stageLabel } from "../labels";
import { usePreviewUrl } from "../usePreviewUrl";
import { confirmCancel, retryJob } from "./jobActions";

function ResultPanel({ job }: { job: PushbroomJob }) {
  const { url, failed } = usePreviewUrl(job.has_preview ? job.id : null, job.finished_at || job.updated_at);
  const [open, setOpen] = useState(false);
  const [packing, setPacking] = useState(false);
  const title = `${formatNo(job.seq)} ${job.title} 结果预览`;

  async function pack() {
    setPacking(true);
    try {
      await api.pushbroomDownloadArchive(job.id);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setPacking(false);
    }
  }

  return (
    <Flex vertical gap={12} className="pb-result">
      <Flex justify="space-between" align="center">
        <Typography.Text strong>结果预览</Typography.Text>
        <Flex gap={8}>
          <Button icon={<ExpandOutlined />} disabled={!url} onClick={() => setOpen(true)}>
            全屏查看
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} loading={packing} onClick={pack}>
            打包下载
          </Button>
        </Flex>
      </Flex>
      <div className="pb-preview-box">
        {url ? (
          <img
            src={url}
            alt={title}
            role="button"
            tabIndex={0}
            onClick={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setOpen(true);
            }}
          />
        ) : failed ? (
          <Typography.Text type="secondary">预览加载失败</Typography.Text>
        ) : job.has_preview ? (
          <Spin />
        ) : (
          <Typography.Text type="secondary">这次没有生成预览，成果文件在下方</Typography.Text>
        )}
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        点图或「全屏查看」可以滚轮缩放、按住拖动。
      </Typography.Text>
      <ImageInspectModal open={open} src={url} title={title} onClose={() => setOpen(false)} />
    </Flex>
  );
}

function RunningPanel({ job, onChanged }: { job: PushbroomJob; onChanged: () => void }) {
  const index = Math.max(0, PB_STAGES.findIndex((s) => s.id === job.current_stage));
  return (
    <Flex vertical gap={16} className="pb-running">
      <Flex justify="space-between" align="center">
        <Typography.Text strong>{job.status === "cancelling" ? "正在取消" : `正在计算：${stageLabel(job.current_stage)}`}</Typography.Text>
        {job.status === "running" && (
          <Button danger onClick={() => confirmCancel(job, onChanged)}>
            取消
          </Button>
        )}
      </Flex>
      <Steps
        size="small"
        labelPlacement="vertical"
        current={index}
        percent={Math.round(job.stage_progress || 0)}
        items={PB_STAGES.map((s) => ({ title: s.label }))}
      />
      <Progress percent={Math.round(job.global_percent || 0)} status="active" />
      {job.message && <Typography.Text type="secondary">{job.message}</Typography.Text>}
    </Flex>
  );
}

export default function JobMain({
  job,
  admin,
  flightName,
  onChanged,
}: {
  job: PushbroomJob | null;
  admin: boolean;
  flightName: string;
  onChanged: () => void;
}) {
  if (!job) {
    return <Empty description="这个架次还没跑过。点右上角「提交拼图」开始。" />;
  }
  const warning = job.calibration_warning ? (
    <Alert
      type="warning"
      showIcon
      message="本架次与所用安装配置不符，航带接缝可能错开。"
      description={
        admin ? (
          <Link to={`/pushbroom/mounts?flight=${encodeURIComponent(flightName)}`}>去安装配置看看这个架次</Link>
        ) : (
          job.calibration_warning
        )
      }
    />
  ) : null;

  let body;
  if (job.status === "queued") {
    const n = job.queue_position ?? 0;
    body = (
      <Flex vertical align="center" gap={12} className="pb-state">
        <Typography.Title level={5} style={{ margin: 0 }}>
          排队中
        </Typography.Title>
        <Typography.Text type="secondary">{n > 0 ? `前面还有 ${n} 个任务` : "下一个就轮到它"}</Typography.Text>
        <Button danger onClick={() => confirmCancel(job, onChanged)}>
          取消
        </Button>
      </Flex>
    );
  } else if (job.status === "running" || job.status === "cancelling") {
    body = <RunningPanel job={job} onChanged={onChanged} />;
  } else if (job.status === "succeeded") {
    body = <ResultPanel job={job} />;
  } else if (job.status === "failed") {
    body = (
      <Flex vertical gap={12}>
        <Alert type="error" showIcon message="失败原因" description={job.error_summary || "没有记录原因，请看运行日志"} />
        <div>
          <Button type="primary" onClick={() => retryJob(job, onChanged)}>
            重跑
          </Button>
        </div>
      </Flex>
    );
  } else {
    body = (
      <Flex vertical align="center" gap={12} className="pb-state">
        <Typography.Title level={5} style={{ margin: 0 }}>
          已取消
        </Typography.Title>
        <Button type="primary" onClick={() => retryJob(job, onChanged)}>
          重跑
        </Button>
      </Flex>
    );
  }
  return (
    <Flex vertical gap={12}>
      {warning}
      {body}
    </Flex>
  );
}