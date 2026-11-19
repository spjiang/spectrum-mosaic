import { useEffect, useState } from "react";
import { Button, Card, DatePicker, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import { api, download } from "../api/client";
import { LIST_PAGE } from "../listPage";

type Customer = { id: number; name: string };
type Release = { id: number; version: string; arch?: string };
type License = {
  id: number;
  license_id: string;
  customer_id: number;
  release_id: number;
  starts_at: string;
  expires_at: string;
  status: string;
  machine_id?: string;
};

function expiresAfterStart(getStart: () => Dayjs | undefined) {
  return async (_: unknown, value: Dayjs) => {
    const start = getStart();
    if (start && value && !value.isAfter(start)) {
      throw new Error("到期时间必须晚于生效时间");
    }
  };
}

export default function LicensesPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [rows, setRows] = useState<License[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<License | null>(null);
  const [saving, setSaving] = useState(false);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  async function load() {
    const [c, r, l] = await Promise.all([
      api<Customer[]>("/api/customers"),
      api<Release[]>("/api/releases"),
      api<License[]>("/api/licenses"),
    ]);
    setCustomers(c);
    setReleases(r);
    setRows(l);
  }

  useEffect(() => {
    load().catch((e) => message.error(String(e)));
  }, []);

  const customerName = (id: number) => customers.find((c) => c.id === id)?.name || String(id);
  const releaseVer = (id: number) => releases.find((r) => r.id === id)?.version || String(id);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>许可签发</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 个
          </Typography.Text>
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            createForm.resetFields();
            createForm.setFieldsValue({ starts_at: dayjs() });
            setCreateOpen(true);
          }}
        >
          签发许可
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "ID", dataIndex: "id", width: 72 },
          {
            title: "许可 ID",
            dataIndex: "license_id",
            width: 320,
            render: (v: string) => (
              <Typography.Text copyable style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                {v}
              </Typography.Text>
            ),
          },
          { title: "客户", dataIndex: "customer_id", render: (id: number) => customerName(id) },
          { title: "版本", dataIndex: "release_id", render: (id: number) => releaseVer(id) },
          {
            title: "机器",
            dataIndex: "machine_id",
            render: (v: string) =>
              v ? (
                <Typography.Text
                  copyable={{ text: v }}
                  className="lease-claims-full"
                  style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
                >
                  {v}
                </Typography.Text>
              ) : (
                "未绑定"
              ),
          },
          {
            title: "生效",
            dataIndex: "starts_at",
            render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
          },
          {
            title: "到期",
            dataIndex: "expires_at",
            render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (s: string) => (
              <Tag color={s === "active" ? "success" : "default"}>{s === "active" ? "有效" : "停用"}</Tag>
            ),
          },
          {
            title: "操作",
            width: 260,
            render: (_, l) => (
              <Space wrap>
                <Button size="small" onClick={() => download(`/api/licenses/${l.id}/download`, `${l.license_id}.lic`)}>
                  下载
                </Button>
                <Button
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => {
                    setEditing(l);
                    editForm.setFieldsValue({
                      status: l.status,
                      starts_at: dayjs(l.starts_at),
                      expires_at: dayjs(l.expires_at),
                      machine_id: l.machine_id || "",
                    });
                    setEditOpen(true);
                  }}
                >
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该许可？"
                  onConfirm={async () => {
                    try {
                      await api(`/api/licenses/${l.id}`, { method: "DELETE" });
                      message.success("已删除");
                      await load();
                    } catch (e: unknown) {
                      message.error(e instanceof Error ? e.message : String(e));
                    }
                  }}
                >
                  <Button size="small" danger icon={<DeleteOutlined />}>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="签发许可"
        open={createOpen}
        confirmLoading={saving}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={async (v) => {
            setSaving(true);
            try {
              await api("/api/licenses", {
                method: "POST",
                body: JSON.stringify({
                  customer_id: v.customer_id,
                  release_id: v.release_id,
                  starts_at: dayjs(v.starts_at).toISOString(),
                  expires_at: dayjs(v.expires_at).toISOString(),
                  machine_id: String(v.machine_id || "").trim(),
                }),
              });
              message.success("已签发许可");
              setCreateOpen(false);
              await load();
            } catch (e: unknown) {
              message.error(e instanceof Error ? e.message : String(e));
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="customer_id" label="客户" rules={[{ required: true, message: "请选择客户" }]}>
            <Select placeholder="选择客户" options={customers.map((c) => ({ value: c.id, label: c.name }))} />
          </Form.Item>
          <Form.Item name="release_id" label="版本" rules={[{ required: true, message: "请选择版本" }]}>
            <Select
              placeholder="选择版本"
              options={releases.map((r) => ({
                value: r.id,
                label: `${r.version}${r.arch ? ` (linux/${r.arch})` : ""}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="starts_at" label="生效时间" rules={[{ required: true, message: "请选择生效时间" }]}>
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="expires_at"
            label="到期时间"
            dependencies={["starts_at"]}
            rules={[
              { required: true, message: "请选择到期时间" },
              { validator: expiresAfterStart(() => createForm.getFieldValue("starts_at")) },
            ]}
          >
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="machine_id"
            label="机器指纹"
            extra="留空可先签发未绑定许可并打交付包；客户运行 start.sh 后把指纹发回，再在编辑里填写并重签。"
            rules={[{ pattern: /^$|^[0-9a-fA-F]{64}$/, message: "须为 64 位十六进制，或留空表示不绑定" }]}
          >
            <Input placeholder="可选：客户机 start.sh 打印的 machine_id" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑许可"
        open={editOpen}
        confirmLoading={saving}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={async (v) => {
            if (!editing) return;
            setSaving(true);
            try {
              await api(`/api/licenses/${editing.id}`, {
                method: "PATCH",
                body: JSON.stringify({
                  status: v.status,
                  starts_at: dayjs(v.starts_at).toISOString(),
                  expires_at: dayjs(v.expires_at).toISOString(),
                  machine_id: String(v.machine_id || "").trim(),
                }),
              });
              message.success("已保存（时间或机器指纹变更会重新签名许可文件）");
              setEditOpen(false);
              await load();
            } catch (e: unknown) {
              message.error(e instanceof Error ? e.message : String(e));
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="status" label="状态" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "active", label: "有效" },
                { value: "disabled", label: "停用" },
              ]}
            />
          </Form.Item>
          <Form.Item name="starts_at" label="生效时间" rules={[{ required: true, message: "请选择生效时间" }]}>
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="expires_at"
            label="到期时间"
            dependencies={["starts_at"]}
            rules={[
              { required: true, message: "请选择到期时间" },
              { validator: expiresAfterStart(() => editForm.getFieldValue("starts_at")) },
            ]}
          >
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="machine_id"
            label="机器指纹"
            rules={[{ pattern: /^$|^[0-9a-fA-F]{64}$/, message: "须为 64 位十六进制，旧许可可留空" }]}
          >
            <Input placeholder="留空表示不绑定；清空已填写的指纹可解绑并重签" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
