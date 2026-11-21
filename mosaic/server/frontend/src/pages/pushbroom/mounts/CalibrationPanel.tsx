import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Form,
  Input,
  Select,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { api, type PushbroomCalibrationTask, type PushbroomFlightItem } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import FlightPickList from "./FlightPickList";
import { flightDay, formatDuration, meters, pixels } from "../labels";

/** 表格按内容收窄列宽，避免中间空一大段 */
const compactCell = () => ({ style: { whiteSpace: "nowrap" as const, width: "1%" } });

const POLL_MS = 3000;
const MAX_FLIGHTS = 8;
const RUN_STATUS: Record<PushbroomCalibrationTask["status"], { text: string; color: string }> = {
  running: { text: "标定中", color: "processing" },
  done: { text: "可保存", color: "success" },
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
  const short = day && day.length >= 10 ? `${day.slice(5, 7)}${day.slice(8, 10)}` : "";
  return { effective_from: day, name: short ? `${short} 安装` : "" };
}

function perFlightLines(run: PushbroomCalibrationTask) {
  return (run.result?.flights || []).map((row, i) => ({
    key: row.flight || `row-${i}`,
    flight: row.flight || "—",
    detail: row.error
      ? row.error
      : row.calibration?.usable
        ? `${meters(row.calibration.along_suggested_m, row.calibration.along_se_m)} · ${pixels(row.calibration.boresight_suggested_px, row.calibration.boresight_se_px)}`
        : row.calibration?.note || "不可用",
  }));
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
      <Card size="small" className="pb-mounts-step-card">
        <Flex align="center" gap={16} wrap="wrap">
          <Spin />
          <Flex vertical gap={4}>
            <Typography.Text strong>正在标定</Typography.Text>
            <Typography.Text type="secondary">{run.flights.join("、")}</Typography.Text>
            <Typography.Text type="secondary">
              已用 {formatDuration(since)}。可以切换页签或离开本页，计算在后台继续。
            </Typography.Text>
          </Flex>
        </Flex>
      </Card>
    );
  }

  if (run.status === "failed") {
    return (
      <Alert type="error" showIcon message="标定失败" description={run.error || "没有记录原因。请重新选择架次后再试。"} />
    );
  }

  if (!combined?.usable) {
    return (
      <Flex vertical gap={12}>
        <Alert
          type="warning"
          showIcon
          message="这组架次不能合并成一条安装配置"
          description={combined?.note || "请只选同一次安装、有往返航带的架次；拆装前后的架次不要放在一起。"}
        />
        <Table
          className="pb-mounts-table"
          size="small"
          pagination={false}
          tableLayout="auto"
          dataSource={perFlightLines(run)}
          columns={[
            { title: "架次", dataIndex: "flight", ellipsis: true, onCell: compactCell },
            { title: "说明", dataIndex: "detail", onCell: () => ({ className: "pb-mounts-cell-wrap" }) },
          ]}
        />
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
    <Flex vertical gap={16}>
      <Card size="small" title="标定结果（只读）" className="pb-mounts-step-card">
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered>
          <Descriptions.Item label="沿航向偏移">{meters(combined.along_offset_m, combined.along_se_m)}</Descriptions.Item>
          <Descriptions.Item label="狭缝中心偏置">{pixels(combined.boresight_px, combined.boresight_se_px)}</Descriptions.Item>
        </Descriptions>
        <Table
          className="pb-mounts-table"
          style={{ marginTop: 12 }}
          size="small"
          pagination={false}
          tableLayout="auto"
          dataSource={perFlightLines(run)}
          columns={[
            { title: "架次", dataIndex: "flight", ellipsis: true, onCell: compactCell },
            { title: "各架次建议值", dataIndex: "detail", onCell: () => ({ className: "pb-mounts-cell-wrap" }) },
          ]}
        />
      </Card>
      {run.run_token ? (
        <Card size="small" title="保存为安装配置" className="pb-mounts-step-card">
          <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
            名称和生效日期可以改；两个安装参数不能手填，只能来自上面的标定结果。生效日期表示：从这个飞行日起，未手动指定的架次会优先用这组值。
          </Typography.Paragraph>
          <Form<SaveValues> key={run.id} form={form} layout="vertical" style={{ maxWidth: 420 }} initialValues={defaults(run, flights)} onFinish={save}>
            <Form.Item name="name" label="配置名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
              <Input maxLength={64} placeholder="例如 0419 安装" />
            </Form.Item>
            <Form.Item name="effective_from" label="生效日期" rules={[{ required: true, message: "请选择日期" }]}>
              <Input type="date" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={saving} block>
                保存为安装配置
              </Button>
            </Form.Item>
          </Form>
        </Card>
      ) : (
        <Alert type="warning" showIcon message="标定结果已超过 30 分钟" description="请回到上一步重新选择架次并开始标定。" />
      )}
    </Flex>
  );
}

/** 新建配置：三步 — 选架次、等待标定、保存 */
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
  const [pickOpen, setPickOpen] = useState(true);

  const busy = runs.some((r) => r.status === "running");
  const current = runs.find((r) => r.id === currentId) ?? runs[0] ?? null;

  const stepIndex = useMemo(() => {
    if (!current) return 0;
    if (current.status === "running") return 1;
    if (current.status === "done" && current.result?.combined?.usable) return 2;
    if (current.status === "failed" || (current.status === "done" && !current.result?.combined?.usable)) return 1;
    return 0;
  }, [current]);

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

  function onPickChange(next: string[]) {
    if (next.length > MAX_FLIGHTS) {
      message.warning(`最多选 ${MAX_FLIGHTS} 个架次`);
      setPicked(next.slice(0, MAX_FLIGHTS));
      return;
    }
    setPicked(next);
  }

  const startDisabledReason = workerOffline
    ? "推扫计算服务未在线"
    : busy
      ? "请等待当前标定结束"
      : picked.length === 0
        ? "先在表格里勾选架次"
        : "";

  async function start() {
    setStarting(true);
    try {
      const run = await api.pushbroomStartCalibration(picked);
      setRuns((cur) => [run, ...cur]);
      setCurrentId(run.id);
      setPicked([]);
      setPickOpen(false);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setStarting(false);
    }
  }

  const runOptions = runs.map((r) => ({
    value: r.id,
    label: `${RUN_STATUS[r.status].text} · ${formatJobDateTime(r.created_at).slice(0, 16)} · ${r.flights.join("、")}`,
  }));

  useEffect(() => {
    if (runs.length && currentId === null) setCurrentId(runs[0].id);
  }, [runs, currentId]);

  return (
    <Flex vertical gap={16} className="pb-mounts-new">
      <Steps
        current={stepIndex}
        items={[{ title: "选择架次" }, { title: "标定" }, { title: "保存配置" }]}
        size="small"
        className="pb-mounts-steps"
      />

      {(pickOpen || stepIndex === 0) && (
        <Card size="small" title="第 1 步：选择标定架次" className="pb-mounts-step-card">
          <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
            勾选同一次相机安装、且有往返航带的架次（最多 {MAX_FLIGHTS} 个）。每个架次大约 1 分钟。拆装前后的架次不要放在同一次标定里。
          </Typography.Paragraph>
          <FlightPickList flights={flights} picked={picked} max={MAX_FLIGHTS} onChange={onPickChange} />
          <Flex align="center" gap={12} style={{ marginTop: 16 }} wrap="wrap">
            <Button type="primary" disabled={Boolean(startDisabledReason)} loading={starting} onClick={start}>
              {picked.length ? `开始标定（已选 ${picked.length} 个）` : "开始标定"}
            </Button>
            {startDisabledReason ? <Typography.Text type="secondary">{startDisabledReason}</Typography.Text> : null}
            {picked.length > 0 && !startDisabledReason ? (
              <Button type="link" onClick={() => setPicked([])}>
                清空选择
              </Button>
            ) : null}
          </Flex>
        </Card>
      )}

      {(current || runs.length > 0) && !pickOpen ? (
        <Flex vertical gap={12}>
          <Flex justify="space-between" align="center" wrap="wrap" gap={8}>
            <Typography.Text strong>{stepIndex >= 2 ? "第 3 步：确认并保存" : "第 2 步：标定结果"}</Typography.Text>
            {!busy ? (
              <Button type="link" onClick={() => setPickOpen(true)}>
                标定另一组架次
              </Button>
            ) : null}
          </Flex>
          {runs.length > 1 ? (
            <Flex align="center" gap={8} wrap="wrap">
              <Typography.Text type="secondary">查看标定记录：</Typography.Text>
              <Select
                className="pb-mounts-run-select"
                style={{ minWidth: 320, flex: 1 }}
                value={current?.id}
                options={runOptions}
                onChange={(id) => setCurrentId(id)}
              />
            </Flex>
          ) : null}
          {current ? <RunResult run={current} flights={flights} onSaved={onSaved} /> : null}
        </Flex>
      ) : null}

      {pickOpen && runs.length === 0 ? (
        <Typography.Text type="secondary">还没有标定记录。选好架次后点「开始标定」。</Typography.Text>
      ) : null}
    </Flex>
  );
}