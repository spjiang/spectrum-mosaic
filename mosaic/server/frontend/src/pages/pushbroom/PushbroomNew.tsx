import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Radio,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { ApiError, api, type PushbroomCheck, type PushbroomStatus } from "../../api";
import { flightSize, formatBytes, formatDuration, parseBandList, wavelengthRange } from "./labels";

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

function CheckResult({ check }: { check: PushbroomCheck }) {
  const f = check.flight;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {check.errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          message="架次核对未通过，不能提交"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {check.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          }
        />
      )}
      {check.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {check.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          }
        />
      )}
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="架次">{check.name}</Descriptions.Item>
        <Descriptions.Item label="目录">
          <Typography.Text copyable style={{ wordBreak: "break-all" }}>
            {check.input_dir}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="影像尺寸">{flightSize(f)}</Descriptions.Item>
        <Descriptions.Item label="波长范围">{wavelengthRange(f)}</Descriptions.Item>
        <Descriptions.Item label="数据量">{formatBytes(f.spe_bytes)}</Descriptions.Item>
        <Descriptions.Item label="定位">
          {f.gps_records != null ? `${f.gps_records} 条，覆盖 ${formatDuration(f.duration_s)}` : "—"}
        </Descriptions.Item>
      </Descriptions>
    </Space>
  );
}

export default function PushbroomNewPage() {
  const nav = useNavigate();
  const [form] = Form.useForm<FormValues>();
  const [status, setStatus] = useState<PushbroomStatus | null>(null);
  const [flights, setFlights] = useState<PushbroomCheck[]>([]);
  const [inputRoot, setInputRoot] = useState("");
  const [outputRoot, setOutputRoot] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<PushbroomCheck | null>(null);
  const [manualPath, setManualPath] = useState("");
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const gsdMode = Form.useWatch("gsd_mode", form);
  const bandMode = Form.useWatch("band_mode", form);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, data] = await Promise.all([api.pushbroomStatus(), api.pushbroomFlights()]);
      setStatus(st);
      setFlights(data.items);
      setInputRoot(data.input_root);
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

  const columns = useMemo(
    () => [
      { title: "架次", dataIndex: "name", ellipsis: true },
      { title: "影像尺寸", render: (_: unknown, r: PushbroomCheck) => flightSize(r.flight) },
      { title: "数据量", width: 110, render: (_: unknown, r: PushbroomCheck) => formatBytes(r.flight.spe_bytes) },
      {
        title: "时长",
        width: 120,
        render: (_: unknown, r: PushbroomCheck) => formatDuration(r.flight.duration_s),
      },
      {
        title: "核对",
        width: 110,
        render: (_: unknown, r: PushbroomCheck) =>
          r.ok ? (
            <Tag color="success">可提交</Tag>
          ) : (
            <Tooltip title={r.errors.join("；")}>
              <Tag color="error">不可用</Tag>
            </Tooltip>
          ),
      },
    ],
    [],
  );

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {status?.worker_online === false && (
        <Alert
          type="warning"
          showIcon
          message="推扫计算服务未在线"
          description="任务可以提交，会在队列里等待。请联系管理员启动推扫计算服务。"
        />
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
        <Typography.Paragraph type="secondary">
          列出 <Typography.Text code>{inputRoot || "—"}</Typography.Text> 下含 figspec.hdr / .spe / .gps 的目录。
          成果写到 <Typography.Text code>{outputRoot || "—"}</Typography.Text>/架次名/提交时间。
        </Typography.Paragraph>
        <Table
          rowKey="input_dir"
          size="middle"
          loading={loading}
          dataSource={flights}
          columns={columns}
          pagination={false}
          locale={{ emptyText: "目录下没有找到推扫架次" }}
          rowSelection={{
            type: "radio",
            selectedRowKeys: selected ? [selected.input_dir] : [],
            onChange: (_keys, rows) => setSelected(rows[0] ?? null),
          }}
          onRow={(r) => ({ onClick: () => setSelected(r), style: { cursor: "pointer" } })}
        />
        <Space.Compact style={{ width: "100%", marginTop: 16 }}>
          <Input
            placeholder={`也可以直接填写架次目录，例如 ${inputRoot || "/data/input/pushbroom"}/FigSpec_xxx`}
            value={manualPath}
            onChange={(e) => setManualPath(e.target.value)}
            onPressEnter={() => checkManual()}
          />
          <Button onClick={() => checkManual()} loading={checking}>
            核对
          </Button>
        </Space.Compact>
      </Card>

      {selected && (
        <Card className="mosaic-panel" title="核对结果">
          <CheckResult check={selected} />
        </Card>
      )}

      <Card className="mosaic-panel" title="拼图参数">
        <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={{ gsd_mode: "auto", band_mode: "all" }}
          onFinish={submit}
          style={{ maxWidth: 640 }}
        >
          <Form.Item name="title" label="任务名称" extra="不填则用架次名">
            <Input maxLength={120} placeholder={selected?.name} />
          </Form.Item>
          <Form.Item name="gsd_mode" label="成果分辨率">
            <Radio.Group>
              <Radio value="auto">自动（按航高和相机估算）</Radio>
              <Radio value="manual">指定</Radio>
            </Radio.Group>
          </Form.Item>
          {gsdMode === "manual" && (
            <Form.Item
              name="gsd_cm"
              label="地面分辨率（厘米/像元）"
              rules={[{ required: true, message: "请填写分辨率" }]}
            >
              <InputNumber min={1} max={200} step={0.5} style={{ width: 200 }} addonAfter="cm" />
            </Form.Item>
          )}
          <Form.Item name="band_mode" label="输出波段" extra="全部波段时成果体积与原始数据相当，可只选需要的波段">
            <Radio.Group>
              <Radio value="all">全部波段</Radio>
              <Radio value="pick">指定波段</Radio>
            </Radio.Group>
          </Form.Item>
          {bandMode === "pick" && (
            <Form.Item
              name="bands_text"
              label="波段序号"
              extra={`从 1 开始，例如 1-10, 48, 150, 208${selected?.flight.bands ? `（共 ${selected.flight.bands} 个）` : ""}`}
              rules={[{ required: true, message: "请填写波段序号" }]}
            >
              <Input placeholder="48, 150, 208" />
            </Form.Item>
          )}
          <Form.Item style={{ marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit" loading={submitting} disabled={!selected?.ok}>
                提交拼图
              </Button>
              {!selected && <Typography.Text type="secondary">请先选择一个架次</Typography.Text>}
              {selected && !selected.ok && <Typography.Text type="danger">当前架次核对未通过</Typography.Text>}
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </Space>
  );
}
