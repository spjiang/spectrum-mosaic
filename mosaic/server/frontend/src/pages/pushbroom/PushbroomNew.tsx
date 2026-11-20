import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Form, Input, InputNumber, Radio, Space, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { ApiError, api, type PushbroomCheck, type PushbroomStatus } from "../../api";
import { flightSize, formatBytes, formatDuration, parseBandList } from "./labels";

type FormValues = {
  title?: string;
  gsd_mode: "auto" | "manual";
  gsd_cm?: number;
  band_mode: "all" | "pick";
  bands_text?: string;
};

function checkFromError(err: unknown): PushbroomCheck | null {
  if (!(err instanceof ApiError)) return null;
  const detail = err.detail as { check?: PushbroomCheck } | undefined;
  return detail?.check ?? null;
}

function reasonOf(flight: PushbroomCheck): string {
  return flight.errors[0] || flight.warnings[0] || "";
}

export default function PushbroomNewPage() {
  const nav = useNavigate();
  const [form] = Form.useForm<FormValues>();
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [flights, setFlights] = useState<PushbroomCheck[]>([]);
  const [outputRoot, setOutputRoot] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<PushbroomCheck | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [more, setMore] = useState(false);
  const gsdMode = Form.useWatch("gsd_mode", form);
  const bandMode = Form.useWatch("band_mode", form);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, data] = await Promise.all([api.pushbroomStatus(), api.pushbroomFlights()]);
      setStatus(st);
      setFlights(data.items);
      setOutputRoot(data.output_root);
      setSelected((cur) => (cur ? data.items.find((x) => x.input_dir === cur.input_dir) ?? cur : null));
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  async function checkManual() {
    const path = manualPath.trim();
    if (!path) {
      message.warning("请填写架次目录");
      return;
    }
    setChecking(true);
    try {
      setSelected(await api.pushbroomCheck(path));
      setManualOpen(false);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setChecking(false);
    }
  }

  async function submit(values: FormValues) {
    if (!selected?.ok) return;
    let bands: number[] | undefined;
    if (values.band_mode === "pick") {
      try {
        bands = parseBandList(values.bands_text || "", selected.flight.bands);
      } catch (err) {
        form.setFields([{ name: "bands_text", errors: [(err as Error).message] }]);
        return;
      }
    }
    const params = {
      ...(values.gsd_mode === "manual" && values.gsd_cm ? { gsd_m: values.gsd_cm / 100 } : {}),
      ...(bands ? { bands } : {}),
    };
    setSubmitting(true);
    try {
      const job = await api.pushbroomCreate({ input_dir: selected.input_dir, title: values.title, params });
      message.success(`推扫任务 #${job.seq} 已提交`);
      nav(`/pushbroom/jobs?job=${job.id}`);
    } catch (err) {
      const check = checkFromError(err);
      if (check) setSelected(check);
      message.error((err as Error).message || String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {status?.worker_online === false && (
        <Alert type="warning" showIcon message="推扫计算服务未在线" description="任务可以提交，会在队列里等待。请联系管理员启动推扫计算服务。" />
      )}
      <Card
        className="mosaic-panel"
        title="选择架次"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>
            刷新
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          点一行即可选中。成果写到 {outputRoot || "输出目录"}/架次名/提交时间。
        </Typography.Paragraph>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {flights.map((flight) => {
            const active = selected?.input_dir === flight.input_dir;
            const why = reasonOf(flight);
            return (
              <div
                key={flight.input_dir}
                role="button"
                tabIndex={0}
                onClick={() => setSelected(flight)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") setSelected(flight);
                }}
                style={{
                  textAlign: "left",
                  border: active ? "1px solid #1677ff" : "1px solid #f0f0f0",
                  background: active ? "#e6f4ff" : "#fff",
                  borderRadius: 8,
                  padding: "12px 16px",
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                  <Typography.Text strong>{flight.name}</Typography.Text>
                  {flight.ok ? <Tag color="success">可提交</Tag> : <Tag color="error">不可用</Tag>}
                </div>
                <Typography.Text type="secondary">
                  {flight.ok
                    ? `${flightSize(flight.flight)} · ${formatBytes(flight.flight.spe_bytes)} · ${formatDuration(flight.flight.duration_s)}`
                    : why || "核对未通过"}
                </Typography.Text>
              </div>
            );
          })}
          {!loading && flights.length === 0 && <Typography.Text type="secondary">目录下没有找到推扫架次</Typography.Text>}
        </div>
        <div style={{ marginTop: 12 }}>
          <Button type="link" style={{ padding: 0 }} onClick={() => setManualOpen((v) => !v)}>
            {manualOpen ? "收起目录输入" : "列表里没有？填写目录"}
          </Button>
          {manualOpen && (
            <Space.Compact style={{ width: "100%", marginTop: 8 }}>
              <Input
                placeholder="/data/input/pushbroom/FigSpec_xxx"
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                onPressEnter={() => checkManual()}
              />
              <Button onClick={() => checkManual()} loading={checking}>
                核对并选中
              </Button>
            </Space.Compact>
          )}
        </div>
      </Card>

      <Card className="mosaic-panel" title={selected ? `提交 ${selected.name}` : "提交"}>
        {!selected && <Alert type="info" showIcon message="先在上面点一个架次" />}
        {selected && !selected.ok && (
          <Alert
            type="error"
            showIcon
            message="这个架次不能提交"
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {selected.errors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            }
          />
        )}
        {selected?.ok && (
          <Form<FormValues> form={form} layout="vertical" initialValues={{ gsd_mode: "auto", band_mode: "all" }} onFinish={submit}>
            <Typography.Paragraph type="secondary">
              {flightSize(selected.flight)} · {formatBytes(selected.flight.spe_bytes)} · {formatDuration(selected.flight.duration_s)}
            </Typography.Paragraph>
            {selected.warnings.length > 0 && (
              <Alert style={{ marginBottom: 16 }} type="warning" showIcon message={selected.warnings.join("；")} />
            )}
            <Form.Item name="title" label="任务名称" extra="不填就用架次名">
              <Input maxLength={120} placeholder={selected.name} />
            </Form.Item>
            <Typography.Paragraph>
              分辨率自动，输出全部波段。
              <Button type="link" onClick={() => setMore((v) => !v)}>
                {more ? "收起" : "要改分辨率或波段"}
              </Button>
            </Typography.Paragraph>
            {more && (
              <>
                <Form.Item name="gsd_mode" label="成果分辨率">
                  <Radio.Group>
                    <Radio value="auto">自动（按航高和相机估算）</Radio>
                    <Radio value="manual">指定</Radio>
                  </Radio.Group>
                </Form.Item>
                {gsdMode === "manual" && (
                  <Form.Item name="gsd_cm" label="地面分辨率（厘米/像元）" rules={[{ required: true, message: "请填写分辨率" }]}>
                    <InputNumber min={1} max={200} step={0.5} style={{ width: 200 }} addonAfter="cm" />
                  </Form.Item>
                )}
                <Form.Item name="band_mode" label="输出波段" extra="全部波段时成果体积与原始数据相当">
                  <Radio.Group>
                    <Radio value="all">全部波段</Radio>
                    <Radio value="pick">指定波段</Radio>
                  </Radio.Group>
                </Form.Item>
                {bandMode === "pick" && (
                  <Form.Item
                    name="bands_text"
                    label="波段序号"
                    extra={`从 1 开始，例如 48, 150, 208${selected.flight.bands ? `（共 ${selected.flight.bands} 个）` : ""}`}
                    rules={[{ required: true, message: "请填写波段序号" }]}
                  >
                    <Input placeholder="48, 150, 208" />
                  </Form.Item>
                )}
              </>
            )}
            <Button type="primary" htmlType="submit" loading={submitting}>
              提交这个架次
            </Button>
          </Form>
        )}
      </Card>
    </Space>
  );
}
