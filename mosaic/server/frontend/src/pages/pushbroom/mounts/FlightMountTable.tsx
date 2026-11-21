import { Button, Card, Table, Typography, message } from "antd";
import { api, type PushbroomFlightItem } from "../../../api";
import MountTag from "../MountTag";
import { MOUNT_SOURCE, flightStartText, meters, pixels } from "../labels";

export default function FlightMountTable({
  flights,
  focus,
  loading,
  onChanged,
}: {
  flights: PushbroomFlightItem[];
  focus: string | null;
  loading: boolean;
  onChanged: () => void;
}) {
  async function undo(item: PushbroomFlightItem) {
    try {
      await api.pushbroomSetFlightMount(item.name, null);
      message.success(`${item.name} 已改回按日期`);
      onChanged();
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  return (
    <Card className="mosaic-panel" title="各架次用哪组">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        手动指定在工作台架次顶部的「改选」里设置，这里可以撤销。
      </Typography.Paragraph>
      <Table
        rowKey="input_dir"
        size="small"
        loading={loading}
        dataSource={flights}
        pagination={false}
        rowClassName={(item) => (item.name === focus ? "pb-row-focus" : "")}
        columns={[
          { title: "架次", dataIndex: "name" },
          { title: "飞行时间", width: 160, render: (_: unknown, f: PushbroomFlightItem) => flightStartText(f.flight) },
          { title: "安装配置", render: (_: unknown, f: PushbroomFlightItem) => <MountTag mount={f.mount} /> },
          {
            title: "数值",
            width: 220,
            render: (_: unknown, f: PushbroomFlightItem) => `${meters(f.mount.along_offset_m)} · ${pixels(f.mount.boresight_px)}`,
          },
          { title: "来源", width: 90, render: (_: unknown, f: PushbroomFlightItem) => MOUNT_SOURCE[f.mount.source] },
          {
            title: "操作",
            width: 110,
            render: (_: unknown, f: PushbroomFlightItem) =>
              f.mount.source === "manual" ? (
                <Button type="link" onClick={() => undo(f)}>
                  撤销手动
                </Button>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}