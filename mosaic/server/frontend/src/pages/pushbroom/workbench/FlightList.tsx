import { useMemo, useState } from "react";
import { Button, Empty, Input, Progress, Segmented, Space, Tag, Typography, message } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { api, type PushbroomFlightItem } from "../../../api";
import MountTag from "../MountTag";
import { PB_ACTIVE, PB_STATUS, flightStartText, formatBytes } from "../labels";
import { usePreviewUrl } from "../usePreviewUrl";

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

function Thumb({ item }: { item: PushbroomFlightItem }) {
  const { url } = usePreviewUrl(item.preview_job_id, item.latest_job?.finished_at);
  if (!url) return <span className="pb-flight-thumb pb-flight-thumb--empty" aria-hidden />;
  return <img className="pb-flight-thumb" src={url} alt={`${item.name} 最近结果`} />;
}

function FlightCard({ item, active, onSelect }: { item: PushbroomFlightItem; active: boolean; onSelect: () => void }) {
  const latest = item.latest_job;
  const status = latest ? PB_STATUS[latest.status] : null;
  const pick = () => item.ok && onSelect();
  return (
    <div
      className={`pb-flight-card${active ? " is-active" : ""}${item.ok ? "" : " is-bad"}`}
      role="button"
      tabIndex={item.ok ? 0 : -1}
      aria-disabled={!item.ok}
      aria-pressed={active}
      onClick={pick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          pick();
        }
      }}
    >
      <Thumb item={item} />
      <div className="pb-flight-body">
        <div className="pb-flight-title">
          <Typography.Text strong ellipsis={{ tooltip: item.name }}>
            {item.name}
          </Typography.Text>
          {status && <Tag color={status.color}>{status.text}</Tag>}
        </div>
        <Typography.Text type="secondary" className="pb-flight-meta">
          {flightStartText(item.flight)} · {formatBytes(item.flight.spe_bytes)}
        </Typography.Text>
        {item.ok ? (
          <div className="pb-flight-meta">
            <MountTag mount={item.mount} />
            <Typography.Text type="secondary">{item.job_count ? `跑过 ${item.job_count} 次` : "未跑过"}</Typography.Text>
          </div>
        ) : (
          <Typography.Text type="danger" className="pb-flight-meta">
            {item.errors[0] || "核对未通过"}
          </Typography.Text>
        )}
        {latest && isActive(item) && <Progress percent={Math.round(latest.global_percent || 0)} size="small" status="active" />}
      </div>
    </div>
  );
}

export default function FlightList({
  items,
  selected,
  loading,
  onSelect,
  onReload,
  onChecked,
}: {
  items: PushbroomFlightItem[];
  selected: string | null;
  loading: boolean;
  onSelect: (name: string) => void;
  onReload: () => void;
  onChecked: (item: PushbroomFlightItem) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [keyword, setKeyword] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
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
  const shown = items.filter((i) => matches(i, filter) && i.name.toLowerCase().includes(keyword.trim().toLowerCase()));

  async function checkManual() {
    const path = manualPath.trim();
    if (!path) {
      message.warning("请填写架次目录");
      return;
    }
    setChecking(true);
    setManualError([]);
    try {
      const item = await api.pushbroomCheck(path);
      if (item.ok) {
        onChecked(item);
        setManualOpen(false);
        setManualPath("");
      } else {
        setManualError(item.errors.length ? item.errors : ["核对未通过"]);
      }
    } catch (err) {
      setManualError([(err as Error).message || String(err)]);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="pb-flight-list">
      <div className="pb-flight-tools">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索架次"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onReload} aria-label="刷新架次" />
      </div>
      <Segmented<Filter>
        block
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
      <div className="pb-flight-scroll">
        {shown.map((item) => (
          <FlightCard key={item.input_dir} item={item} active={item.name === selected} onSelect={() => onSelect(item.name)} />
        ))}
        {!loading && shown.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的架次" />}
      </div>
      <div className="pb-flight-manual">
        <Button type="link" style={{ padding: 0 }} onClick={() => setManualOpen((v) => !v)}>
          {manualOpen ? "收起目录输入" : "列表里没有？填写目录"}
        </Button>
        {manualOpen && (
          <>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                placeholder="/data/input/pushbroom/FigSpec_xxx"
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                onPressEnter={() => checkManual()}
              />
              <Button onClick={() => checkManual()} loading={checking}>
                核对
              </Button>
            </Space.Compact>
            {manualError.map((line) => (
              <Typography.Text key={line} type="danger" className="pb-flight-meta">
                {line}
              </Typography.Text>
            ))}
          </>
        )}
      </div>
    </div>
  );
}