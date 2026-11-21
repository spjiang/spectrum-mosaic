import { useMemo, useState } from "react";
import { Button, Empty, Flex, Input, Modal, Progress, Segmented, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { api, type PushbroomFlightItem, type PushbroomJob } from "../../../api";
import { ImageInspectModal } from "../../../components/ImageStage";
import MountTag from "../MountTag";
import { PB_ACTIVE, PB_STATUS, flightStartText, formatBytes } from "../labels";
import { usePreviewUrl } from "../usePreviewUrl";
import { matchMountKeyword, mountsTablePagination } from "../mounts/listTools";
import FlightRowMenu from "./FlightRowMenu";

type Filter = "all" | "active" | "never" | "bad";

function isActive(item: PushbroomFlightItem) {
  return Boolean(item.latest_job && PB_ACTIVE.includes(item.latest_job.status));
}

function matches(item: PushbroomFlightItem, filter: Filter) {
  if (filter === "active") return isActive(item);
  if (filter === "never") return item.ok && item.job_count === 0;
  if (filter === "bad") return !item.ok;
  return true;
}

function ThumbCell({ item }: { item: PushbroomFlightItem }) {
  const { url } = usePreviewUrl(item.preview_job_id, item.latest_job?.finished_at);
  const [open, setOpen] = useState(false);
  if (!url) return <span className="pb-flight-thumb pb-flight-thumb--empty" aria-hidden />;
  const title = `${item.name} 结果预览`;
  return (
    <>
      <button
        type="button"
        className="pb-flight-thumb-btn"
        aria-label={`${title}，点击放大`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        <img className="pb-flight-thumb" src={url} alt={title} />
      </button>
      <ImageInspectModal open={open} src={url} title={title} onClose={() => setOpen(false)} />
    </>
  );
}

/** 架次列表：表格 + 分页，替代左侧卡片 */
export default function FlightList({
  items,
  selected,
  loading,
  admin,
  onSelect,
  onReload,
  onChecked,
  onSubmitted,
  onMountChanged,
}: {
  items: PushbroomFlightItem[];
  selected: string | null;
  loading: boolean;
  admin: boolean;
  onSelect: (name: string) => void;
  onReload: () => void;
  onChecked: (item: PushbroomFlightItem) => void;
  onSubmitted: (job: PushbroomJob) => void;
  onMountChanged: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [keyword, setKeyword] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [manualError, setManualError] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);

  const counts = useMemo(
    () => ({
      all: items.length,
      active: items.filter((i) => matches(i, "active")).length,
      never: items.filter((i) => matches(i, "never")).length,
      bad: items.filter((i) => matches(i, "bad")).length,
    }),
    [items],
  );

  const rows = useMemo(
    () => items.filter((i) => matches(i, filter) && matchMountKeyword(i.name, keyword)),
    [filter, items, keyword],
  );

  /** 核对手动目录；成功返回 true 供弹窗关闭 */
  async function checkManual(): Promise<boolean> {
    const path = manualPath.trim();
    if (!path) {
      message.warning("请填写架次目录");
      return false;
    }
    setChecking(true);
    setManualError([]);
    try {
      const item = await api.pushbroomCheck(path);
      if (item.ok) {
        onChecked(item);
        setManualPath("");
        setManualError([]);
        return true;
      }
      setManualError(item.errors.length ? item.errors : ["核对未通过"]);
      return false;
    } catch (err) {
      setManualError([(err as Error).message || String(err)]);
      return false;
    } finally {
      setChecking(false);
    }
  }

  const columns: ColumnsType<PushbroomFlightItem> = [
    {
      title: "预览",
      onCell: () => ({ className: "jobs-preview-cell" }),
      render: (_: unknown, item) => <ThumbCell item={item} />,
    },
    {
      title: "架次",
      dataIndex: "name",
      ellipsis: true,
      render: (name: string, item) =>
        item.ok ? (
          <Button type="link" size="small" style={{ padding: 0, height: "auto" }} onClick={() => onSelect(name)}>
            {name}
          </Button>
        ) : (
          <Typography.Text type="secondary">{name}</Typography.Text>
        ),
    },
    {
      title: "飞行时间",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, item) => flightStartText(item.flight),
    },
    {
      title: "数据大小",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, item) => formatBytes(item.flight.spe_bytes),
    },
    {
      title: "安装配置",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, item) => (item.ok ? <MountTag mount={item.mount} /> : "—"),
    },
    {
      title: "跑过",
      align: "center",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, item) => (item.ok ? item.job_count : "—"),
    },
    {
      title: "最近状态",
      ellipsis: { showTitle: false },
      render: (_: unknown, item) => {
        if (!item.ok) {
          const msg = item.errors[0] || "不可用";
          return (
            <Typography.Text type="danger" ellipsis={{ tooltip: msg }}>
              {msg}
            </Typography.Text>
          );
        }
        const latest = item.latest_job;
        if (!latest) return <Typography.Text type="secondary">未跑过</Typography.Text>;
        const s = PB_STATUS[latest.status];
        return <Tag color={s.color}>{s.text}</Tag>;
      },
    },
    {
      title: "进度",
      onCell: () => ({ style: { minWidth: 88, maxWidth: 140 } }),
      render: (_: unknown, item) =>
        item.latest_job && isActive(item) ? (
          <Progress percent={Math.round(item.latest_job.global_percent || 0)} size="small" status="active" />
        ) : (
          "—"
        ),
    },
    {
      title: "操作",
      onCell: () => ({ className: "jobs-actions-cell", style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, item) => (
        <FlightRowMenu
          flight={item}
          admin={admin}
          onOpenFlight={() => item.ok && onSelect(item.name)}
          onSubmitted={onSubmitted}
          onMountChanged={onMountChanged}
        />
      ),
    },
  ];

  return (
    <div className="pb-flight-list pb-flight-list--table">
      <Flex className="pb-flight-tools" gap={8} wrap align="center">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索架次"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 220 }}
        />
        <Segmented<Filter>
          size="small"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: `全部 ${counts.all}` },
            { value: "active", label: `运行中 ${counts.active}` },
            { value: "never", label: `未跑过 ${counts.never}` },
            { value: "bad", label: `不可用 ${counts.bad}` },
          ]}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onReload}>
          刷新
        </Button>
        <span style={{ flex: 1, minWidth: 8 }} />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
          新增架次
        </Button>
      </Flex>
      <Modal
        title="新增架次"
        open={addOpen}
        destroyOnClose
        confirmLoading={checking}
        okText="核对并打开"
        onOk={() =>
          checkManual().then((ok) => {
            if (!ok) return Promise.reject();
          })
        }
        onCancel={() => {
          setAddOpen(false);
          setManualError([]);
        }}
        afterOpenChange={(open) => {
          if (!open) setManualPath("");
        }}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          扫描列表里没有的架次，填写服务器上的目录路径；核对通过后会加入列表并打开。
        </Typography.Paragraph>
        <Input
          placeholder="/data/input/pushbroom/FigSpec_xxx"
          value={manualPath}
          onChange={(e) => setManualPath(e.target.value)}
          onPressEnter={() => checkManual()}
        />
        {manualError.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {manualError.map((line) => (
              <Typography.Paragraph key={line} type="danger" style={{ marginBottom: 4 }}>
                {line}
              </Typography.Paragraph>
            ))}
          </div>
        )}
      </Modal>
      <div className="pb-mounts-panel">
      <Table
        className="pb-mounts-table"
        rowKey="input_dir"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={mountsTablePagination(rows.length)}
        tableLayout="auto"
        rowClassName={(item) => {
          if (item.name === selected) return "pb-row-focus";
          if (!item.ok) return "pb-row-bad";
          return "";
        }}
        onRow={(item) => ({
          onClick: (e) => {
            const el = e.target as HTMLElement;
            if (el.closest("button, a, .ant-popover, .job-action-popover, .ant-dropdown, .ant-modal, .image-inspect-modal")) return;
            if (item.ok) onSelect(item.name);
          },
        })}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的架次" /> }}
      />
      </div>
    </div>
  );
}