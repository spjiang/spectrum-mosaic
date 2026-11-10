import { useEffect, useState } from "react";
import { Modal, Typography, message } from "antd";
import { api } from "../api";
import { formatNo } from "../ids";
import { JobSnapshotBody } from "./JobSnapshot";

export default function JobParamsModal({
  open,
  jobId,
  onClose,
}: {
  open: boolean;
  jobId?: string;
  onClose: () => void;
}) {
  const [job, setJob] = useState<any>(null);

  useEffect(() => {
    if (!open || !jobId) {
      setJob(null);
      return;
    }
    let cancelled = false;
    api
      .job(jobId)
      .then((detail) => {
        if (!cancelled) setJob(detail);
      })
      .catch((e: any) => {
        if (!cancelled) message.error(e.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, jobId]);

  const title = job
    ? `参数详情 · ${formatNo(job.seq)}${job.title ? ` ${job.title}` : ""}`
    : "参数详情";

  return (
    <Modal
      open={open}
      title={title}
      width={960}
      footer={null}
      onCancel={onClose}
      destroyOnClose
      styles={{ body: { maxHeight: "72vh", overflow: "auto" } }}
    >
      {!job ? (
        <Typography.Text type="secondary">正在加载…</Typography.Text>
      ) : (
        <JobSnapshotBody
          snapshot={job.params_snapshot}
          profileId={job.profile_id}
          profileVersion={job.profile_version}
        />
      )}
    </Modal>
  );
}
