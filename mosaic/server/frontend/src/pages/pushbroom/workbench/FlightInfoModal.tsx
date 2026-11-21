import { Alert, Descriptions, Modal, Typography } from "antd";
import type { PushbroomFlightItem } from "../../../api";
import MountTag from "../MountTag";
import { flightSize, flightStartText, formatBytes, formatDuration, meters, pixels } from "../labels";

/** 架次基本信息弹窗 */
export default function FlightInfoModal({
  flight,
  open,
  onClose,
}: {
  flight: PushbroomFlightItem | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!flight) return null;
  const f = flight.flight;
  return (
    <Modal title={flight.name} open={open} onCancel={onClose} footer={null} width={560}>
      {!flight.ok && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message="架次不可用" description={flight.errors.join("；")} />
      )}
      {flight.warnings.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={flight.warnings.join("；")} />
      )}
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="目录">
          <Typography.Text copyable style={{ wordBreak: "break-all" }}>
            {flight.input_dir}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="飞行时间">{flightStartText(f)} 起飞</Descriptions.Item>
        <Descriptions.Item label="飞行时长">{formatDuration(f.duration_s)}</Descriptions.Item>
        <Descriptions.Item label="影像尺寸">{flightSize(f)}</Descriptions.Item>
        <Descriptions.Item label="数据大小">{formatBytes(f.spe_bytes)}</Descriptions.Item>
        <Descriptions.Item label="安装配置">
          <MountTag mount={flight.mount} /> · 沿航向 {meters(flight.mount.along_offset_m)} · 狭缝中心 {pixels(flight.mount.boresight_px)}
        </Descriptions.Item>
        <Descriptions.Item label="历史任务">{flight.job_count} 次</Descriptions.Item>
      </Descriptions>
    </Modal>
  );
}