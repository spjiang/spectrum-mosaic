import { useEffect, useMemo, useState } from "react";
import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { api } from "../api";
import { LIST_PAGE } from "../listPage";
import { ROLE_LABEL, ROLE_ORDER } from "../roles";

type Account = {
  id: number;
  username: string;
  is_active: boolean;
  roles: string[];
  created_at?: string | null;
};

function formatTime(v?: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function UsersPage() {
  const [rows, setRows] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  async function reload() {
    setLoading(true);
    try {
      setRows(await api.users());
    } catch (e: any) {
      message.error(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload().catch(() => undefined);
  }, []);

  const filtered = useMemo(() => {
    const k = keyword.trim().toLowerCase();
    if (!k) return rows;
    return rows.filter((r) => {
      const roleText = (r.roles || []).map((id) => ROLE_LABEL[id] || id).join(" ");
      return `${r.username} ${roleText}`.toLowerCase().includes(k);
    });
  }, [rows, keyword]);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>用户管理</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 个
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          <Input.Search
            allowClear
            placeholder="搜索用户名"
            style={{ width: 220 }}
            onSearch={setKeyword}
            onChange={(e) => !e.target.value && setKeyword("")}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={filtered}
        pagination={LIST_PAGE}
        columns={[
          { title: "用户名", dataIndex: "username", width: 180 },
          {
            title: "角色",
            dataIndex: "roles",
            width: 160,
            render: (roles: string[]) => ROLE_LABEL[roles?.[0]] || "—",
          },
          {
            title: "状态",
            dataIndex: "is_active",
            width: 100,
            render: (v: boolean) => <Tag color={v ? "success" : "default"}>{v ? "启用" : "停用"}</Tag>,
          },
          {
            title: "创建时间",
            dataIndex: "created_at",
            width: 180,
            render: (v: string) => formatTime(v),
          },
          {
            title: "操作",
            width: 100,
            render: (_: unknown, r: Account) => (
              <Button
                type="link"
                icon={<EditOutlined />}
                style={{ padding: 0 }}
                onClick={() => {
                  setEditing(r);
                  editForm.setFieldsValue({
                    role: r.roles?.[0],
                    is_active: r.is_active ? "1" : "0",
                    password: "",
                  });
                }}
              >
                编辑
              </Button>
            ),
          },
        ]}
      />

      <Modal
        title="新建用户"
        open={createOpen}
        okText="确定"
        cancelText="取消"
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
              await api.createUser({ username: v.username, password: v.password, roles: [v.role] });
              message.success("已保存");
              createForm.resetFields();
              setCreateOpen(false);
              await reload();
            } catch (e: any) {
              message.error(e.message);
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input autoFocus maxLength={32} />
          </Form.Item>
          <Form.Item name="password" label="登录密码" rules={[{ required: true, message: "请输入登录密码" }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="viewer" rules={[{ required: true, message: "请选择角色" }]}>
            <Select options={ROLE_ORDER.map((id) => ({ value: id, label: ROLE_LABEL[id] }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑用户"
        open={Boolean(editing)}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onCancel={() => setEditing(null)}
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
              await api.updateUser(editing.id, {
                roles: [v.role],
                is_active: v.is_active === "1",
                password: v.password || undefined,
              });
              message.success("已保存");
              setEditing(null);
              await reload();
            } catch (e: any) {
              message.error(e.message);
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item label="用户名">
            <Input value={editing?.username} disabled />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: "请选择角色" }]}>
            <Select options={ROLE_ORDER.map((id) => ({ value: id, label: ROLE_LABEL[id] }))} />
          </Form.Item>
          <Form.Item name="is_active" label="状态" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "1", label: "启用" },
                { value: "0", label: "停用" },
              ]}
            />
          </Form.Item>
          <Form.Item name="password" label="登录密码">
            <Input.Password placeholder="不修改请留空" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
