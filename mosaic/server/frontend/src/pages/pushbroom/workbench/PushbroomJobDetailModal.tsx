import { useEffect, useState } from "react";
import { Modal, Spin } from "antd";
import { api, type PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import JobTabs from "./JobTabs";

/** 任务详情弹窗：成果、参数、耗时、日志 */
export default function PushbroomJobDetailModal({
  jobId,
  open,
  defaultTab,
  onClose,
}: {
  jobId: string | null;
  open: boolean;
  defaultTab?: string;
  onClose: () => void;
}) {
  const [job, setJob] = useState<PushbroomJob | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !jobId) {
      setJob(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .pushbroomJob(jobId)
      .then((j) => !cancelled && setJob(j))
      .catch(() => !cancelled && setJob(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, jobId]);

  return (
    <Modal
      title={job ? `任务 ${formatNo(job.seq)} · 详情` : "任务详情"}
      open={open}
      onCancel={onClose}
      footer={null}
      width={920}
      destroyOnClose
      styles={{ body: { maxHeight: "70vh", overflow: "auto" } }}
    >
      {loading || !job ? <Spin /> : <JobTabs job={job} defaultActiveKey={defaultTab} />}
    </Modal>
  );
}