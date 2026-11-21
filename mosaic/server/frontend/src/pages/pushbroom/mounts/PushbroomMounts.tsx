import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Flex, Tabs, Typography, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomMount, type PushbroomStatus } from "../../../api";
import CalibrationPanel from "./CalibrationPanel";
import FlightMountTable from "./FlightMountTable";
import MountTable from "./MountTable";

type TabKey = "list" | "flights" | "new";

/** 安装配置：分三个页签，避免一屏堆三张表 */
export default function PushbroomMountsPage() {
  const [params, setParams] = useSearchParams();
  const focus = params.get("flight");
  const tabParam = params.get("tab");
  const [tab, setTab] = useState<TabKey>(() => {
    if (tabParam === "new" || tabParam === "calibrate") return "new";
    if (focus) return "flights";
    return "list";
  });
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [mounts, setMounts] = useState<PushbroomMount[]>([]);
  const [builtin, setBuiltin] = useState<{ along_offset_m: number; boresight_px: number } | null>(null);
  const [flights, setFlights] = useState<PushbroomFlightItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, m, f] = await Promise.all([api.pushbroomStatus(), api.pushbroomMounts(), api.pushbroomFlights()]);
      setStatus(st);
      setMounts(m.items);
      setBuiltin(m.builtin);
      setFlights(f.items.filter((item) => item.ok));
    } catch {
      message.error("加载失败，请点刷新或稍后再试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (focus) setTab("flights");
  }, [focus]);

  const tabItems = useMemo(
    () => [
      {
        key: "list",
        label: `配置列表${mounts.length ? `（${mounts.length}）` : ""}`,
        children: <MountTable mounts={mounts} builtin={builtin} loading={loading} onChanged={load} />,
      },
      {
        key: "flights",
        label: `架次对应${flights.length ? `（${flights.length}）` : ""}`,
        children: <FlightMountTable flights={flights} focus={focus} loading={loading} onChanged={load} />,
      },
      {
        key: "new",
        label: "新建配置",
        children: (
          <CalibrationPanel
            flights={flights}
            workerOffline={status?.worker_online === false}
            onSaved={() => {
              load().catch(() => undefined);
              setTab("list");
            }}
          />
        ),
      },
    ],
    [builtin, flights, focus, loading, mounts, load, status?.worker_online],
  );

  function onTabChange(key: string) {
    const next = key as TabKey;
    setTab(next);
    const q = new URLSearchParams(params);
    if (next === "new") q.set("tab", "new");
    else q.delete("tab");
    setParams(q, { replace: true });
  }

  return (
    <Flex vertical gap={12} className="pb-mounts-page">
      <Flex vertical gap={4}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          安装配置
        </Typography.Title>
        <Typography.Text type="secondary">
          相机相对定位点的安装参数。日常在「配置列表」查看；拆装相机后在「新建配置」标定并保存；个别架次在「架次对应」或工作台里「改选」。
        </Typography.Text>
      </Flex>
      {status?.worker_online === false && (
        <Alert type="warning" showIcon message="推扫计算服务未在线" description="现在不能标定。提交拼图仍可排队，等计算节点恢复后再标定。" />
      )}
      <Tabs activeKey={tab} onChange={onTabChange} className="pb-mounts-tabs" items={tabItems} />
    </Flex>
  );
}