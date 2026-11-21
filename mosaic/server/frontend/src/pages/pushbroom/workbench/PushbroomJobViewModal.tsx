import { useEffect, useState } from "react";
import { Modal, Spin } from "antd";
import { api, type PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import { PB_ACTIVE } from "../labels";
import JobMain from "./JobMain";

/** 结果预览 / 运行进度弹窗 */
export default function PushbroomJobViewModal({
  jobId,
  flightName,
  admin,
  open,
  onClose,
  onChanged,
}: {
  jobId: string | null;
  flightName: string;
  admin: boolean;
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [job, setJob] = useState<PushbroomJob | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !jobId) {
      setJob(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const j = await api.pushbroomJob(jobId);
        if (!cancelled) setJob(j);
      } catch {
        /* 忽略单次刷新失败 */
      }
    };
    setLoading(true);
    load().finally(() => !cancelled && setLoading(false));
    const timer = window.setInterval(() => {
      if (cancelled) return;
      load();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, jobId]);

  const titleSuffix = job && PB_ACTIVE.includes(job.status) ? "进度" : "预览";

  return (
    <Modal
      title={job ? `任务 ${formatNo(job.seq)} · ${titleSuffix}` : "任务预览"}
      open={open}
      onCancel={onClose}
      footer={null}
      width={880}
      destroyOnClose
    >
      {loading && !job ? (
        <Spin />
      ) : (
        <JobMain job={job} admin={admin} flightName={flightName} onChanged={onChanged} />
      )}
    </Modal>
  );
}