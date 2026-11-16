import { useEffect, useState } from "react";
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { api } from "../api/client";
import { LIST_PAGE } from "../listPage";

type Customer = {
  id: number;
  name: string;
  contact: string;
  status: string;
  notes: string;
};

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
  const [form] = Form.useForm();

  async function load() {
    setRows(await api<Customer[]>("/api/customers"));
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
        </Form>
      </Modal>
    </Card>
  );
}
