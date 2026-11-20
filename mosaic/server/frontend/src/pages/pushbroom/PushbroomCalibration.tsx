import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Modal, Space, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api, type PushbroomCalibrationRun, type PushbroomCalibrationState, type PushbroomCheck, type PushbroomStatus } from "../../api";
import { formatBytes, formatDuration } from "./labels";

function meters(value: number, se?: number) {
  const text = `${value.toFixed(2)} m`;
  return se == null ? text : `${value.toFixed(2)} ± ${se.toFixed(2)} m`;
}

function pixels(value: number, se?: number) {
  const text = `${value.toFixed(1)} 像元`;
  return se == null ? text : `${value.toFixed(1)} ± ${se.toFixed(1)} 像元`;
}

function usableNames(run: PushbroomCalibrationRun) {
  return run.flights.filter((item) => item.calibration?.usable && item.flight).map((item) => item.flight as string);
}

export default function PushbroomCalibrationPage() {
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [current, setCurrent] = useState<PushbroomCalibrationState | null>(null);
  const [flights, setFlights] = useState<PushbroomCheck[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [run, setRun] = useState<PushbroomCalibrationRun | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, cal, data] = await Promise.all([api.pushbroomStatus(), api.pushbroomCalibration(), api.pushbroomFlights()]);
      setStatus(st);
      setCurrent(cal);
      setFlights(data.items);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const offline = status?.worker_online === false;
  const combined = run?.combined;
  const canApply = Boolean(combined?.usable && run?.run_token && combined.along_offset_m != null && combined.boresight_px != null);
  const picked = flights.filter((item) => selected.includes(item.input_dir));

  function toggle(flight: PushbroomCheck) {
    if (!flight.ok || running) return;
    setRun(null);
    setSelected((cur) => (cur.includes(flight.input_dir) ? cur.filter((item) => item !== flight.input_dir) : [...cur, flight.input_dir]));
  }

  async function start() {
    setRunning(true);
    setRun(null);
    try {
      setRun(await api.pushbroomCalibrate(selected));
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setRunning(false);
    }
  }

  function apply() {
    if (!run?.run_token || !combined?.usable) return;
    const names = usableNames(run);
    Modal.confirm({
      title: "应用到之后的任务",
      content: "之后新建的推扫任务使用这组值。正在计算和已完成的任务不变。",
      okText: "应用",
      cancelText: "取消",
      onOk: async () => {
        setApplying(true);
        try {
          const next = await api.pushbroomApplyCalibration({
            along_offset_m: combined.along_offset_m as number,
            boresight_px: combined.boresight_px as number,
            along_se_m: combined.along_se_m as number,
            boresight_se_px: combined.boresight_se_px as number,
            flights: names,
            run_token: run.run_token as string,
          });
          setCurrent(next);
          setRun(null);
          setSelected([]);
          message.success("已保存。之后新建的任务使用这组值。");
        } catch (err) {
          message.error((err as Error).message || String(err));
        } finally {
          setApplying(false);
        }
      },
    });
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {offline && <Alert type="warning" showIcon message="推扫计算服务未在线，现在不能标定" />}
      <Card className="mosaic-panel" title="现在用的安装参数" extra={<Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新</Button>}>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          这是相机相对定位点的安装，不是飞控参数。拆过相机或云台后，用下面的架次重新标定。
        </Typography.Paragraph>
        {current && !current.active && (
          <Descriptions size="small" column={3} bordered>
            <Descriptions.Item label="沿航向偏移">{meters(current.builtin?.along_offset_m ?? 1.7)}</Descriptions.Item>
            <Descriptions.Item label="狭缝中心偏置">{pixels(current.builtin?.boresight_px ?? -19)}</Descriptions.Item>
            <Descriptions.Item label="来源">尚未标定，使用内置值</Descriptions.Item>
          </Descriptions>
        )}
        {current?.active && (
          <Descriptions size="small" column={3} bordered>
            <Descriptions.Item label="沿航向偏移">{meters(current.along_offset_m ?? 0)}</Descriptions.Item>
            <Descriptions.Item label="狭缝中心偏置">{pixels(current.boresight_px ?? 0)}</Descriptions.Item>
            <Descriptions.Item label="来源">
              {current.created_by || "管理员"}
              {current.created_at ? ` · ${current.created_at.replace("T", " ").slice(0, 16)}` : ""}
              {current.flights?.length ? ` · ${current.flights.join("、")}` : ""}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card className="mosaic-panel" title="1. 点选架次">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          点一行加入，再点一次取消。拆装前后的架次不要放在一起。每个架次大约 1 分钟。
        </Typography.Paragraph>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {flights.map((flight) => {
            const on = selected.includes(flight.input_dir);
            return (
              <div
                key={flight.input_dir}
                role="button"
                tabIndex={flight.ok ? 0 : -1}
                aria-disabled={!flight.ok}
                onClick={() => toggle(flight)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") toggle(flight);
                }}
                style={{
                  textAlign: "left",
                  border: on ? "1px solid #1677ff" : "1px solid #f0f0f0",
                  background: on ? "#e6f4ff" : "#fff",
                  borderRadius: 8,
                  padding: "12px 16px",
                  cursor: flight.ok ? "pointer" : "not-allowed",
                  opacity: flight.ok ? 1 : 0.55,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                  <Typography.Text strong>{on ? "已选 · " : ""}{flight.name}</Typography.Text>
                  {flight.ok ? <Tag>{formatBytes(flight.flight.spe_bytes)} · {formatDuration(flight.flight.duration_s)}</Tag> : <Tag color="error">不能用来标定</Tag>}
                </div>
                {!flight.ok && <Typography.Text type="secondary">{flight.errors[0] || "核对未通过"}</Typography.Text>}
              </div>
            );
          })}
          {!loading && flights.length === 0 && <Typography.Text type="secondary">没有找到架次</Typography.Text>}
        </div>
        <div style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 12 }}>
          <Button type="primary" disabled={offline || picked.length === 0} loading={running} onClick={() => start()}>
            {picked.length === 0 ? "先点选架次" : `标定这 ${picked.length} 个架次`}
          </Button>
          {running && <Typography.Text type="secondary">正在计算，请留在本页</Typography.Text>}
          {!running && picked.length > 0 && <Typography.Text type="secondary">{picked.map((item) => item.name).join("、")}</Typography.Text>}
        </div>
      </Card>

      {run && (
        <Card className="mosaic-panel" title="2. 建议值">
          {combined?.usable ? (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Alert type="success" showIcon message="这组架次可以标定。确认后，只影响之后新建的任务。" />
              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label="沿航向偏移">{meters(combined.along_offset_m ?? 0, combined.along_se_m)}</Descriptions.Item>
                <Descriptions.Item label="狭缝中心偏置">{pixels(combined.boresight_px ?? 0, combined.boresight_se_px)}</Descriptions.Item>
              </Descriptions>
              <ResultList run={run} />
              <Button type="primary" disabled={!canApply} loading={applying} onClick={apply}>
                应用到之后的任务
              </Button>
            </Space>
          ) : (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Alert type="warning" showIcon message="这组架次不能一起标定" description={combined?.note || "换有往返航带、且安装状态相同的架次"} />
              <ResultList run={run} />
            </Space>
          )}
        </Card>
      )}
    </Space>
  );
}

function ResultList({ run }: { run: PushbroomCalibrationRun }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {run.flights.map((row) => (
        <div key={row.flight || row.error} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <Typography.Text>{row.flight || "—"}</Typography.Text>
          <Typography.Text type="secondary">
            {row.error
              ? row.error
              : row.calibration?.usable
                ? `${meters(row.calibration.along_suggested_m ?? 0, row.calibration.along_se_m ?? undefined)} · ${pixels(row.calibration.boresight_suggested_px ?? 0, row.calibration.boresight_se_px ?? undefined)}`
                : "不可用"}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}
