import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Space, Tag, Typography, message } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api/client";

type Setting = {
  key: string;
  value: string;
  label: string;
  description: string;
  used_by: string;
  updated_at: string;
};

export default function SystemConfigPage() {
  const [rows, setRows] = useState<Setting[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  async function load() {
    setLoading(true);
    try {
      const data = await api<Setting[]>("/api/settings/config");
      setRows(data);
      const values: Record<string, string> = {};
      for (const row of data) values[row.key] = row.value;
      form.setFieldsValue(values);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <Card
      className="mosaic-panel"
      loading={loading}
      title={
        <Space>
          <span>全局配置</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            发版台统一配置 · 打交付包时下发到客户 compose
          </Typography.Text>
        </Space>
      }
      extra={
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>
          保存
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="修改后对新打的交付包生效；已部署客户需替换 compose / 环境变量并重启 worker。"
      />
      <Form
        form={form}
        layout="vertical"
        onFinish={async (values) => {
          setSaving(true);
          try {
            const items = rows.map((row) => ({ key: row.key, value: String(values[row.key] ?? "") }));
            const updated = await api<Setting[]>("/api/settings/config", {
              method: "PUT",
              body: JSON.stringify({ items }),
            });
            setRows(updated);
            message.success("已保存全局配置");
          } catch (e: unknown) {
            message.error(e instanceof Error ? e.message : String(e));
          } finally {
            setSaving(false);
          }
        }}
      >
        {rows.map((row) => (
          <Card
            key={row.key}
            size="small"
            style={{ marginBottom: 12 }}
            title={
              <Space wrap>
                <span>{row.label}</span>
                <Tag>{row.key}</Tag>
              </Space>
            }
          >
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              {row.description}
            </Typography.Paragraph>
            <Form.Item
              name={row.key}
              label="配置值"
              rules={[{ required: true, message: "请填写配置值" }]}
              extra={
                row.updated_at ? `上次更新 ${dayjs(row.updated_at).format("YYYY-MM-DD HH:mm:ss")}` : undefined
              }
            >
              <Input style={{ maxWidth: 320 }} />
            </Form.Item>
            <Alert
              type="warning"
              showIcon
              message="使用位置"
              description={<Typography.Text style={{ whiteSpace: "pre-wrap" }}>{row.used_by}</Typography.Text>}
            />
          </Card>
        ))}
      </Form>
    </Card>
  );
}
