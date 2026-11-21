import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Spin, Tag, Typography, message } from "antd";
import { api, type PushbroomCalibrationTask, type PushbroomFlightItem } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import { flightDay, formatDuration, meters, pixels } from "../labels";

const POLL_MS = 3000;
const RUN_STATUS: Record<PushbroomCalibrationTask["status"], { text: string; color: string }> = {
  running: { text: "标定中", color: "processing" },
  done: { text: "已完成", color: "success" },
  failed: { text: "失败", color: "error" },
};

type SaveValues = { name: string; effective_from: string };

function defaults(run: PushbroomCalibrationTask, flights: PushbroomFlightItem[]): SaveValues {
  const days = run.flights
    .map((name) => flights.find((f) => f.name === name))
    .map((f) => (f ? flightDay(f.flight) : ""))
    .filter(Boolean)
    .sort();
  const day = days[0] || "";
  return { effective_from: day, name: day ? `${day.slice(5, 7)}${day.slice(8, 10)} 安装` : "" };
}

function ResultList({ run }: { run: PushbroomCalibrationTask }) {
  return (
    <Flex vertical gap={6}>
      {(run.result?.flights || []).map((row) => (
        <Flex key={row.flight || row.error} justify="space-between" gap={12}>
          <Typography.Text>{row.flight || "—"}</Typography.Text>
          <Typography.Text type="secondary">
            {row.error
              ? row.error
              : row.calibration?.usable
                ? `${meters(row.calibration.along_suggested_m, row.calibration.along_se_m)} · ${pixels(row.calibration.boresight_suggested_px, row.calibration.boresight_se_px)}`
                : row.calibration?.note || "不可用"}
          </Typography.Text>
        </Flex>
      ))}
    </Flex>
  );
}

function RunResult({
  run,
  flights,
  onSaved,
}: {
  run: PushbroomCalibrationTask;
  flights: PushbroomFlightItem[];
  onSaved: () => void;
}) {
  const [form] = Form.useForm<SaveValues>();
  const [saving, setSaving] = useState(false);
  const combined = run.result?.combined;

  if (run.status === "running") {
    const since = run.created_at ? (Date.now() - Date.parse(run.created_at)) / 1000 : null;
    return (
      <Flex align="center" gap={12}>
        <Spin />
        <Typography.Text>
          正在标定 {run.flights.join("、")}，已用 {formatDuration(since)}。离开本页不影响计算。
        </Typography.Text>
      </Flex>
    );
  }
  if (run.status === "failed") {
    return <Alert type="error" showIcon message="标定失败" description={run.error || "没有记录原因"} />;
  }
  if (!combined?.usable) {
    return (
      <Flex vertical gap={12}>
        <Alert type="warning" showIcon message="这组架次不能一起标定" description={combined?.note || "换有往返航带、且安装状态相同的架次"} />
        <ResultList run={run} />
      </Flex>
    );
  }

  async function save(values: SaveValues) {
    if (!run.run_token) return;
    setSaving(true);
    try {
      await api.pushbroomCreateMount({
        run_id: run.id,
        run_token: run.run_token,
        name: values.name.trim(),
        effective_from: values.effective_from,
      });
      message.success("已保存。之后提交的任务按生效日期使用这组配置。");
      onSaved();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Flex vertical gap={12}>
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="沿航向偏移">{meters(combined.along_offset_m, combined.along_se_m)}</Descriptions.Item>
        <Descriptions.Item label="狭缝中心偏置">{pixels(combined.boresight_px, combined.boresight_se_px)}</Descriptions.Item>
      </Descriptions>
      <ResultList run={run} />
      {run.run_token ? (
        <Form<SaveValues> key={run.id} form={form} layout="inline" initialValues={defaults(run, flights)} onFinish={save}>
          <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="effective_from" label="生效日期" rules={[{ required: true, message: "请选择日期" }]}>
            <Input type="date" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存为安装配置
            </Button>
          </Form.Item>
        </Form>
      ) : (
        <Alert type="warning" showIcon message="标定结果已超过 30 分钟，请重新标定后再保存" />
      )}
    </Flex>
  );
}

export default function CalibrationPanel({
  flights,
  workerOffline,
  onSaved,
}: {
  flights: PushbroomFlightItem[];
  workerOffline: boolean;
  onSaved: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [runs, setRuns] = useState<PushbroomCalibrationTask[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const busy = runs.some((r) => r.status === "running");
  const current = runs.find((r) => r.id === currentId) ?? runs[0] ?? null;

  const loadRuns = useCallback(async () => {
    const data = await api.pushbroomCalibrationRuns();
    setRuns(data.items);
  }, []);

  useEffect(() => {
    loadRuns().catch((err) => message.error((err as Error).message || String(err)));
  }, [loadRuns]);

  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => loadRuns().catch(() => undefined), POLL_MS);
    return () => window.clearInterval(t);
  }, [busy, loadRuns]);

  async function start() {
    setStarting(true);
    try {
      const run = await api.pushbroomStartCalibration(picked);
      setRuns((cur) => [run, ...cur]);
      setCurrentId(run.id);
      setPicked([]);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setStarting(false);
    }
  }

  const reason = workerOffline ? "推扫计算服务未在线" : busy ? "已有一次标定在进行" : picked.length === 0 ? "先勾选架次" : "";

  return (
    <Card className="mosaic-panel" title="新建安装配置">
      <Flex vertical gap={16}>
        <Typography.Text type="secondary">
          勾选同一次安装、有往返航带的架次。拆装前后的架次不要放在一起。每个架次大约 1 分钟，离开本页不影响计算。
        </Typography.Text>
        <Checkbox.Group
          value={picked}
          onChange={(v) => setPicked(v as string[])}
          options={flights.map((f) => ({ value: f.input_dir, label: `${f.name}（${flightDay(f.flight) || "日期未知"}）` }))}
        />
        <Flex align="center" gap={12}>
          <Button type="primary" disabled={Boolean(reason)} loading={starting} onClick={start}>
            {picked.length ? `标定这 ${picked.length} 个架次` : "开始标定"}
          </Button>
          {reason && <Typography.Text type="secondary">{reason}</Typography.Text>}
        </Flex>
        {runs.length > 0 && (
          <Flex vertical gap={6}>
            <Typography.Text strong>最近的标定</Typography.Text>
            {runs.map((r) => (
              <Flex
                key={r.id}
                gap={8}
                align="center"
                className={`pb-history-item${current?.id === r.id ? " is-active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => setCurrentId(r.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") setCurrentId(r.id);
                }}
              >
                <Tag color={RUN_STATUS[r.status].color}>{RUN_STATUS[r.status].text}</Tag>
                <Typography.Text>{formatJobDateTime(r.created_at).slice(0, 16)}</Typography.Text>
                <Typography.Text type="secondary" ellipsis>
                  {r.flights.join("、")}
                </Typography.Text>
              </Flex>
            ))}
          </Flex>
        )}
        {current && <RunResult run={current} flights={flights} onSaved={onSaved} />}
      </Flex>
    </Card>
  );
}