import { useCallback, useEffect, useState } from "react";
import { Button, Card, Form, Input, Tabs, Typography, message } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { api } from "../api";

type WorkerEnv = {
  rabbitmq_url: string;
  pythonpath: string;
  workdir: string;
  mplbackend: string;
  data_roots: string;
  default_input_dir: string;
  default_output_dir: string;
  source: string;
};

type WorkerEnvPayload = {
  worker_env: WorkerEnv;
  can_edit: boolean;
};

function WorkerEnvPanel() {
  const [data, setData] = useState<WorkerEnvPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const apply = useCallback(
    (s: WorkerEnvPayload) => {
      setData(s);
      form.setFieldsValue({
        data_roots: s.worker_env.data_roots,
        default_input_dir: s.worker_env.default_input_dir,
        default_output_dir: s.worker_env.default_output_dir,
      });
    },
    [form]
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      apply(await api.getWorkerEnv());
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    reload().catch((e) => {
      setLoading(false);
      message.error(String(e));
    });
  }, [reload]);

  const onSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const s = await api.updateWorkerEnv({
        data_roots: values.data_roots || "",
        default_input_dir: values.default_input_dir || "",
        default_output_dir: values.default_output_dir || "",
      });
      apply(s);
      message.success("已保存。路径规则立即生效；RabbitMQ 等容器变量改 compose 后需重启 Worker");
    } catch (e: any) {
      message.error(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const canEdit = !!data?.can_edit;
  const env = data?.worker_env;

  return (
    <Card
      className="mosaic-panel"
      bordered={false}
      loading={loading && !env}
      extra={
        canEdit ? (
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => onSave()}>
            保存
          </Button>
        ) : null
      }
    >
      <Typography.Paragraph type="secondary">
        Worker 仍映射主程序源码（容器 /app）。数据目录与 worker 同级：宿主机{" "}
        <Typography.Text code>server/data</Typography.Text> → 容器{" "}
        <Typography.Text code>/data</Typography.Text>。输入{" "}
        <Typography.Text code>/data/input/测区名</Typography.Text>，输出{" "}
        <Typography.Text code>/data/output/runs/任务名</Typography.Text>
        。连通性见 <Link to="/health">健康检查</Link>。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" disabled={!canEdit} style={{ maxWidth: 720 }}>
        <Typography.Title level={5}>容器环境（只读）</Typography.Title>
        <Form.Item label="RABBITMQ_URL">
          <Input value={env?.rabbitmq_url} disabled />
        </Form.Item>
        <Form.Item label="PYTHONPATH">
          <Input value={env?.pythonpath} disabled />
        </Form.Item>
        <Form.Item label="工作目录">
          <Input value={env?.workdir} disabled />
        </Form.Item>
        <Form.Item label="MPLBACKEND">
          <Input value={env?.mplbackend} disabled />
        </Form.Item>
        <Typography.Title level={5}>数据路径</Typography.Title>
        <Form.Item
          name="data_roots"
          label="DATA_ROOTS"
          extra="固定为容器 /data，对应宿主机 server/data（与 worker 同级）"
        >
          <Input placeholder="/data" disabled />
        </Form.Item>
        <Form.Item
          name="default_input_dir"
          label="DEFAULT_INPUT_DIR"
          extra="容器路径，例如 /data/input/MAX_20251017/MAX_20251017_001"
        >
          <Input placeholder="/data/input/MAX_20251017/MAX_20251017_001" />
        </Form.Item>
        <Form.Item
          name="default_output_dir"
          label="DEFAULT_OUTPUT_DIR"
          extra="容器路径，例如 /data/output/runs"
        >
          <Input placeholder="/data/output/runs" />
        </Form.Item>
      </Form>
      {env && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          来源：{env.source === "db" ? "数据库（已保存路径）" : "容器环境变量"}
          {!canEdit ? " · 当前账号无配置修改权限" : ""}
        </Typography.Text>
      )}
    </Card>
  );
}

export default function SettingsPage() {
  return (
    <Card className="mosaic-panel" title="系统配置">
      <Tabs
        defaultActiveKey="worker"
        items={[
          {
            key: "worker",
            label: "计算环境",
            children: <WorkerEnvPanel />,
          },
        ]}
      />
    </Card>
  );
}
