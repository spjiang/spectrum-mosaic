import { Alert, Modal, Spin } from "antd";
import type { PushbroomFlightItem, PushbroomJob } from "../../../api";
import JobHistory from "./JobHistory";

/** 架次历次任务：居中弹窗（不再在页面下方叠卡片） */
export default function FlightJobsModal({
  flightName,
  flight,
  loadingFlights,
  jobs,
  open,
  onClose,
  onDetail,
  onPreview,
  onDetailFiles,
  onChanged,
}: {
  flightName: string;
  flight: PushbroomFlightItem | null;
  loadingFlights: boolean;
  jobs: PushbroomJob[];
  open: boolean;
  onClose: () => void;
  onDetail: (jobId: string) => void;
  onPreview: (jobId: string) => void;
  onDetailFiles: (jobId: string) => void;
  onChanged: () => void;
}) {
  return (
    <Modal
      title={`${flightName} · 历次任务`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={920}
      destroyOnClose
      styles={{ body: { maxHeight: "70vh", overflow: "auto", paddingTop: 8 } }}
    >
      {loadingFlights && !flight ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 32 }}>
          <Spin tip="加载架次与任务…" />
        </div>
      ) : null}
      {!loadingFlights && !flight ? (
        <Alert type="warning" showIcon message="未找到该架次" description="可能目录不可用或名称有误，请关闭后在架次列表里重新选择。" />
      ) : null}
      {flight ? (
        <JobHistory
          jobs={jobs}
          onDetail={onDetail}
          onPreview={onPreview}
          onDetailFiles={onDetailFiles}
          onChanged={onChanged}
        />
      ) : null}
    </Modal>
  );
}