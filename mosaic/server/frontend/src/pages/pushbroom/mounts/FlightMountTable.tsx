import { useMemo, useState } from "react";
import { Button, Flex, Input, Select, Space, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { PushbroomMountSource } from "../../../api";
import { api, type PushbroomFlightItem } from "../../../api";
import MountPickerModal from "../MountPickerModal";
import MountTag from "../MountTag";
import { MOUNT_SOURCE, flightStartText, meters, pixels } from "../labels";
import { matchMountKeyword, mountsTablePagination } from "./listTools";

type SourceFilter = "all" | PushbroomMountSource;

/** 各架次当前安装配置；可改选或撤销手动 */
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
  const [editFlight, setEditFlight] = useState<PushbroomFlightItem | null>(null);
  const [keyword, setKeyword] = useState("");
  const [source, setSource] = useState<SourceFilter>("all");

  const rows = useMemo(() => {
    return flights.filter((f) => {
      if (!matchMountKeyword(f.name, keyword)) return false;
      if (source !== "all" && f.mount.source !== source) return false;
      return true;
    });
  }, [flights, keyword, source]);

  async function undo(item: PushbroomFlightItem) {
    try {
      await api.pushbroomSetFlightMount(item.name, null);
      message.success(`${item.name} 已改回按日期`);
      onChanged();
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  const columns: ColumnsType<PushbroomFlightItem> = [
    { title: "架次", dataIndex: "name", ellipsis: true },
    { title: "飞行时间", render: (_: unknown, f) => flightStartText(f.flight) },
    { title: "当前配置", render: (_: unknown, f) => <MountTag mount={f.mount} /> },
    {
      title: "安装参数",
      render: (_: unknown, f) => `${meters(f.mount.along_offset_m)} · ${pixels(f.mount.boresight_px)}`,
    },
    { title: "来源", render: (_: unknown, f) => MOUNT_SOURCE[f.mount.source] },
    {
      title: "操作",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, f) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => setEditFlight(f)}>
            改选
          </Button>
          {f.mount.source === "manual" ? (
            <Button type="link" size="small" onClick={() => undo(f)}>
              撤销手动
            </Button>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <div className="pb-mounts-panel">
      <p className="pb-mounts-hint">
        默认按飞行日期自动选配置。手动改选会写入数据库，之后该架次一直用你指定的那组，直到撤销或改回自动。
      </p>
      <Flex className="pb-mounts-toolbar" gap={8} wrap align="center">
        <Input
          allowClear
          placeholder="搜索架次名"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 200 }}
        />
        <Select<SourceFilter>
          value={source}
          onChange={setSource}
          style={{ width: 140 }}
          options={[
            { value: "all", label: "全部来源" },
            { value: "date", label: "按日期" },
            { value: "manual", label: "手动" },
            { value: "builtin", label: "内置" },
          ]}
        />
        {(keyword || source !== "all") && (
          <Button
            type="link"
            onClick={() => {
              setKeyword("");
              setSource("all");
            }}
          >
            清空筛选
          </Button>
        )}
      </Flex>
      <Table
        className="pb-mounts-table"
        rowKey="input_dir"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={mountsTablePagination(rows.length)}
        tableLayout="auto"
        rowClassName={(item) => (item.name === focus ? "pb-row-focus" : "")}
        locale={{ emptyText: flights.length ? "没有符合筛选条件的架次" : "没有可用架次" }}
        columns={columns}
      />
      {editFlight && (
        <MountPickerModal
          flight={editFlight}
          open={Boolean(editFlight)}
          onClose={() => setEditFlight(null)}
          onDone={onChanged}
        />
      )}
    </div>
  );
}