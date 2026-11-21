import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Flex, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomMount, type PushbroomStatus } from "../../../api";
import CalibrationPanel from "./CalibrationPanel";
import FlightMountTable from "./FlightMountTable";
import MountTable from "./MountTable";

export default function PushbroomMountsPage() {
  const [params] = useSearchParams();
  const focus = params.get("flight");
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
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  return (
    <Flex vertical gap={16}>
      {status?.worker_online === false && <Alert type="warning" showIcon message="推扫计算服务未在线，现在不能标定" />}
      <MountTable mounts={mounts} builtin={builtin} loading={loading} onChanged={load} />
      <FlightMountTable flights={flights} focus={focus} loading={loading} onChanged={load} />
      <CalibrationPanel flights={flights} workerOffline={status?.worker_online === false} onSaved={load} />
    </Flex>
  );
}