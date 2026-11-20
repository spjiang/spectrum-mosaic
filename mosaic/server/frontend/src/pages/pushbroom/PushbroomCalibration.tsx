import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Modal, Space, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api, type PushbroomCalibrationRun, type PushbroomCalibrationState, type PushbroomCheck, type PushbroomStatus } from "../../api";

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
      setFlights(data.items.filter((item) => item.ok));
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
          message.success("已保存。之后新建的任务使用这组值，无需重启计算服务。");
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
      {offline && (
        <Alert type="warning" showIcon message="推扫计算服务未在线" description="标定需要计算服务在线。请联系管理员启动推扫计算服务。" />
      )}
      <Card className="mosaic-panel" title="当前生效" extra={<Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新</Button>}>
        <Typography.Paragraph type="secondary">
          这两个值描述相机相对定位点的安装，不是飞控参数。拆装或更换相机后重新标定。
        </Typography.Paragraph>
        {current && !current.active && (
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="沿航向偏移">{meters(current.builtin?.along_offset_m ?? 1.7)}</Descriptions.Item>
            <Descriptions.Item label="狭缝中心偏置">{pixels(current.builtin?.boresight_px ?? -19)}</Descriptions.Item>
            <Descriptions.Item label="来源">尚未标定，使用内置值</Descriptions.Item>
          </Descriptions>
        )}
        {current?.active && (
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="沿航向偏移">{meters(current.along_offset_m ?? 0)}</Descriptions.Item>
            <Descriptions.Item label="狭缝中心偏置">{pixels(current.boresight_px ?? 0)}</Descriptions.Item>
            <Descriptions.Item label="来源">
              相机标定
              {current.created_at ? ` · ${current.created_at.replace("T", " ").slice(0, 16)}` : ""}
              {current.created_by ? ` · ${current.created_by}` : ""}
              {current.flights?.length ? ` · ${current.flights.join("、")}` : ""}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card className="mosaic-panel" title="标定">
        <Typography.Paragraph type="secondary">选择有往返航带的架次。每个架次大约 1 分钟，请留在本页。数字由标定算出，不能手填。</Typography.Paragraph>
        <Table
          rowKey="input_dir"
          size="middle"
          loading={loading}
          dataSource={flights}
          pagination={false}
          locale={{ emptyText: "没有核对通过的架次" }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => {
              setSelected(keys.map(String));
              setRun(null);
            },
          }}
          columns={[
            { title: "架次", dataIndex: "name" },
            { title: "目录", dataIndex: "input_dir", ellipsis: true },
          ]}
        />
        <Button type="primary" style={{ marginTop: 16 }} disabled={offline || selected.length === 0} loading={running} onClick={() => start()}>
          开始标定
        </Button>
      </Card>

      {run && (
        <Card className="mosaic-panel" title="标定结果">
          {combined?.usable ? (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="沿航向偏移">{meters(combined.along_offset_m ?? 0, combined.along_se_m)}</Descriptions.Item>
                <Descriptions.Item label="狭缝中心偏置">{pixels(combined.boresight_px ?? 0, combined.boresight_se_px)}</Descriptions.Item>
              </Descriptions>
              <FlightTable run={run} />
              <Button type="primary" disabled={!canApply} loading={applying} onClick={apply}>
                应用到之后的任务
              </Button>
            </Space>
          ) : (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Alert type="warning" showIcon message="这组架次不能用来标定" description={combined?.note || "请换有往返航带、且安装状态相同的架次"} />
              <FlightTable run={run} />
            </Space>
          )}
        </Card>
      )}
    </Space>
  );
}

function FlightTable({ run }: { run: PushbroomCalibrationRun }) {
  return (
    <Table
      size="small"
      pagination={false}
      rowKey={(row) => row.flight || row.error || "flight"}
      dataSource={run.flights}
      columns={[
        { title: "架次", dataIndex: "flight", render: (name: string | undefined) => name || "—" },
        {
          title: "沿航向偏移",
          render: (_: unknown, row: PushbroomCalibrationRun["flights"][number]) =>
            row.calibration?.along_suggested_m == null ? "—" : meters(row.calibration.along_suggested_m, row.calibration.along_se_m ?? undefined),
        },
        {
          title: "狭缝中心偏置",
          render: (_: unknown, row: PushbroomCalibrationRun["flights"][number]) =>
            row.calibration?.boresight_suggested_px == null
              ? "—"
              : pixels(row.calibration.boresight_suggested_px, row.calibration.boresight_se_px ?? undefined),
        },
        {
          title: "结果",
          render: (_: unknown, row: PushbroomCalibrationRun["flights"][number]) =>
            row.error ? <Tag color="error">{row.error}</Tag> : row.calibration?.usable ? <Tag color="success">可用</Tag> : <Tag>不可用</Tag>,
        },
      ]}
    />
  );
}
