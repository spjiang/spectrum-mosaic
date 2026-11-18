import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { api } from "../api/client";
import { LIST_PAGE } from "../listPage";

type Customer = {
  id: number;
  name: string;
  contact: string;
  status: string;
  notes: string;
  clock_tolerance_hours: string;
  lease_hours: string;
  lease_interval_hours: string;
  effective_clock_tolerance_hours: string;
  effective_lease_hours: string;
  effective_lease_interval_hours: string;
};

type Setting = { key: string; value: string };

const statusMap: Record<string, { color: string; label: string }> = {
  trial: { color: "processing", label: "试用" },
  formal: { color: "success", label: "正式" },
  disabled: { color: "default", label: "停用" },
};

export default function CustomersPage() {
  const [rows, setRows] = useState<Customer[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [saving, setSaving] = useState(false);
  const [globals, setGlobals] = useState<Record<string, string>>({});
  const [form] = Form.useForm();

  async function load() {
    const [customers, settings] = await Promise.all([
      api<Customer[]>("/api/customers"),
      api<Setting[]>("/api/settings/config"),
    ]);
    setRows(customers);
    const values: Record<string, string> = {};
    for (const item of settings) values[item.key] = item.value;
    setGlobals(values);
  }

  useEffect(() => {
    load().catch((e) => message.error(String(e)));
  }, []);

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ status: "trial" });
    setOpen(true);
  }

  function openEdit(row: Customer) {
    setEditing(row);
    form.setFieldsValue(row);
    setOpen(true);
  }

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>客户档案</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 个
          </Typography.Text>
        </Space>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建客户
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          { title: "单位", dataIndex: "name" },
          { title: "联系人", dataIndex: "contact", render: (v) => v || "—" },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (s: string) => {
              const m = statusMap[s] || { color: "default", label: s };
              return <Tag color={m.color}>{m.label}</Tag>;
            },
          },
          { title: "备注", dataIndex: "notes", render: (v) => v || "—" },
          {
            title: "运行参数",
            width: 220,
            render: (_, row) => {
              const custom = row.lease_hours || row.lease_interval_hours || row.clock_tolerance_hours;
              return (
                <Space size={4} wrap>
                  <span>
                    租约 {row.effective_lease_hours}h · 间隔 {row.effective_lease_interval_hours}h
                  </span>
                  <Tag color={custom ? "gold" : "default"}>{custom ? "独立" : "全局"}</Tag>
                </Space>
              );
            },
          },
          {
            title: "操作",
            width: 160,
            render: (_, row) => (
              <Space>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该客户？"
                  onConfirm={async () => {
                    try {
                      await api(`/api/customers/${row.id}`, { method: "DELETE" });
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
        title={editing ? "编辑客户" : "新建客户"}
        open={open}
        width={640}
        confirmLoading={saving}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ status: "trial" }}
          onFinish={async (v) => {
            setSaving(true);
            try {
              if (editing) {
                await api(`/api/customers/${editing.id}`, { method: "PATCH", body: JSON.stringify(v) });
                message.success("已保存");
              } else {
                await api("/api/customers", { method: "POST", body: JSON.stringify(v) });
                message.success("已新建客户");
              }
              setOpen(false);
              await load();
            } catch (e: unknown) {
              message.error(e instanceof Error ? e.message : String(e));
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="name" label="单位名" rules={[{ required: true, message: "请填写单位名" }]}>
            <Input autoFocus placeholder="客户单位名称" />
          </Form.Item>
          <Form.Item name="contact" label="联系人">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="status" label="状态" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "trial", label: "试用" },
                { value: "formal", label: "正式" },
                { value: "disabled", label: "停用" },
              ]}
            />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={3} placeholder="可选" />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="留空则使用全局配置。填写后该客户优先使用自己的值。租约有效期在下一次领取时生效；回拨容差和领取间隔在重新打交付包后生效。"
          />
          <Form.Item name="lease_hours" label="租约有效期（小时）">
            <Input allowClear placeholder={`留空使用全局 ${globals.license_lease_hours || "72"}`} />
          </Form.Item>
          <Form.Item name="lease_interval_hours" label="租约领取间隔（小时）">
            <Input allowClear placeholder={`留空使用全局 ${globals.license_lease_interval_hours || "6"}`} />
          </Form.Item>
          <Form.Item name="clock_tolerance_hours" label="回拨容差（小时）">
            <Input allowClear placeholder={`留空使用全局 ${globals.license_clock_tolerance_hours || "1"}`} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
