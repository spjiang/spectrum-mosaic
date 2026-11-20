import { useEffect, useState } from "react";
import { Button, Card, Checkbox, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { api, download } from "../api/client";
import { LIST_PAGE } from "../listPage";

type Release = {
  id: number;
  version: string;
  arch: string;
  image_name: string;
  digest: string;
  status: string;
  error_message: string;
  created_at: string;
};

type ReleaseArtifact = {
  name: string;
  label: string;
  filename: string;
  size_bytes: number;
  available: boolean;
};

function formatBytes(n: number): string {
  if (!n || n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

const ARCH_OPTIONS = [
  { value: "amd64", label: "linux/amd64" },
  { value: "arm64", label: "linux/arm64" },
];

const statusColor: Record<string, string> = {
  pending: "default",
  running: "processing",
  success: "success",
  failed: "error",
};

export default function ReleasesPage() {
  const [rows, setRows] = useState<Release[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Release | null>(null);
  const [saving, setSaving] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logRow, setLogRow] = useState<Release | null>(null);
  const [logText, setLogText] = useState("");
  const [logLoading, setLogLoading] = useState(false);
  const [dlOpen, setDlOpen] = useState(false);
  const [dlRow, setDlRow] = useState<Release | null>(null);
  const [dlItems, setDlItems] = useState<ReleaseArtifact[]>([]);
  const [dlSelected, setDlSelected] = useState<string[]>([]);
  const [dlLoading, setDlLoading] = useState(false);
  const [dlSaving, setDlSaving] = useState(false);
  const [form] = Form.useForm();

  async function load() {
    setRows(await api<Release[]>("/api/releases"));
  }

  async function fetchLog(id: number) {
    const token = localStorage.getItem("release_token");
    const resp = await fetch(`/api/releases/${id}/log`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    return resp.text();
  }

  useEffect(() => {
    load().catch((e) => message.error(String(e)));
    const t = setInterval(() => load().catch(() => undefined), 4000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!logOpen || !logRow) return;
    const latest = rows.find((r) => r.id === logRow.id);
    if (latest && latest.status !== logRow.status) setLogRow(latest);
  }, [rows, logOpen, logRow]);

  useEffect(() => {
    if (!logOpen || !logRow) return;
    let cancelled = false;
    const pull = () => {
      fetchLog(logRow.id)
        .then((text) => {
          if (!cancelled) setLogText(text);
        })
        .catch(() => undefined)
        .finally(() => {
          if (!cancelled) setLogLoading(false);
        });
    };
    setLogLoading(true);
    pull();
    const live = logRow.status === "running";
    const t = live ? setInterval(pull, 1500) : undefined;
    return () => {
      cancelled = true;
      if (t) clearInterval(t);
    };
  }, [logOpen, logRow?.id, logRow?.status]);

  function openCreate() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }

  function openEdit(row: Release) {
    if (row.status === "running") {
      message.warning("构建进行中，不能编辑");
      return;
    }
    setEditing(row);
    form.setFieldsValue({ version: row.version });
    setOpen(true);
  }

  function openLog(row: Release) {
    setLogRow(row);
    setLogText("");
    setLogOpen(true);
  }

  async function openDownload(row: Release) {
    setDlRow(row);
    setDlItems([]);
    setDlSelected([]);
    setDlOpen(true);
    setDlLoading(true);
    try {
      const items = await api<ReleaseArtifact[]>(`/api/releases/${row.id}/artifacts`);
      setDlItems(items);
      setDlSelected(items.filter((i) => i.available).map((i) => i.name));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
      setDlOpen(false);
    } finally {
      setDlLoading(false);
    }
  }

  async function confirmDownload() {
    if (!dlRow) return;
    const chosen = dlItems.filter((i) => i.available && dlSelected.includes(i.name));
    if (!chosen.length) {
      message.warning("请至少选择一个可用镜像");
      return;
    }
    setDlSaving(true);
    try {
      for (let i = 0; i < chosen.length; i += 1) {
        const item = chosen[i];
        const filename = `${item.name}-${dlRow.version}-${dlRow.arch || "arch"}.tar`;
        await download(`/api/releases/${dlRow.id}/artifacts/${item.name}`, filename);
        if (i < chosen.length - 1) {
          await new Promise((r) => setTimeout(r, 400));
        }
      }
      message.success(`已开始下载 ${chosen.length} 个镜像`);
      setDlOpen(false);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setDlSaving(false);
    }
  }

  function confirmBuild(row: Release) {
    Modal.confirm({
      title: "确认构建",
      content: `开始构建版本 ${row.version}（linux/${row.arch}，离线全量：worker/推扫 worker/backend/frontend + postgres/rabbitmq）？构建期间不可编辑或删除。`,
      okText: "开始构建",
      cancelText: "取消",
      onOk: async () => {
        try {
          await api(`/api/releases/${row.id}/build`, { method: "POST" });
          message.info("已开始构建");
          await load();
        } catch (e: unknown) {
          message.error(e instanceof Error ? e.message : String(e));
          throw e;
        }
      },
    });
  }

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>版本构建</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 个
          </Typography.Text>
        </Space>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          登记版本
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        tableLayout="auto"
        columns={[
          { title: "ID", dataIndex: "id" },
          { title: "版本", dataIndex: "version" },
          {
            title: "架构",
            dataIndex: "arch",
            render: (a: string) => (a ? `linux/${a}` : "—"),
          },
          {
            title: "状态",
            dataIndex: "status",
            render: (s: string, r) => (
              <Space direction="vertical" size={0}>
                <Tag color={statusColor[s] || "default"}>{s}</Tag>
                {r.error_message ? (
                  <Typography.Text type="danger" style={{ fontSize: 12 }}>
                    {r.error_message}
                  </Typography.Text>
                ) : null}
              </Space>
            ),
          },
          {
            title: "镜像",
            dataIndex: "image_name",
            render: (v: string) =>
              v ? (
                <Typography.Paragraph
                  style={{ marginBottom: 0, whiteSpace: "pre-wrap", fontSize: 12, fontFamily: "ui-monospace, monospace" }}
                  copyable={{ text: v }}
                >
                  {v}
                </Typography.Paragraph>
              ) : (
                "—"
              ),
          },
          {
            title: "摘要",
            dataIndex: "digest",
            ellipsis: true,
            render: (v: string) => (
              <span style={{ whiteSpace: "nowrap" }}>{v || "—"}</span>
            ),
          },
          {
            title: "操作",
            render: (_, r) => (
              <Space wrap size={[4, 4]} style={{ whiteSpace: "nowrap" }}>
                <Button size="small" type="primary" disabled={r.status === "running"} onClick={() => confirmBuild(r)}>
                  构建
                </Button>
                <Button size="small" onClick={() => openLog(r)}>
                  日志
                </Button>
                <Button size="small" onClick={() => openDownload(r)}>
                  下载
                </Button>
                <Button size="small" icon={<EditOutlined />} disabled={r.status === "running"} onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该版本？"
                  disabled={r.status === "running"}
                  onConfirm={async () => {
                    try {
                      await api(`/api/releases/${r.id}`, { method: "DELETE" });
                      message.success("已删除");
                      await load();
                    } catch (e: unknown) {
                      message.error(e instanceof Error ? e.message : String(e));
                    }
                  }}
                >
                  <Button size="small" danger icon={<DeleteOutlined />} disabled={r.status === "running"}>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={logRow ? `构建日志 · ${logRow.version} (linux/${logRow.arch})` : "构建日志"}
        open={logOpen}
        onCancel={() => setLogOpen(false)}
        footer={
          <Button type="primary" onClick={() => setLogOpen(false)}>
            关闭
          </Button>
        }
        width={860}
        destroyOnClose
        styles={{ body: { maxHeight: "calc(100vh - 220px)", overflow: "auto" } }}
      >
        {logLoading && !logText ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : (
          <pre className="mosaic-log-box" style={{ marginTop: 0, maxHeight: "none" }}>
            {logText || "暂无日志"}
          </pre>
        )}
      </Modal>

      <Modal
        title={dlRow ? `下载镜像 · ${dlRow.version} (linux/${dlRow.arch})` : "下载镜像"}
        open={dlOpen}
        confirmLoading={dlSaving}
        okText="下载所选"
        cancelText="取消"
        onCancel={() => setDlOpen(false)}
        onOk={() => confirmDownload()}
        destroyOnClose
      >
        {dlLoading ? (
          <Typography.Text type="secondary">加载产物列表…</Typography.Text>
        ) : (
          <Checkbox.Group
            style={{ width: "100%" }}
            value={dlSelected}
            onChange={(vals) => setDlSelected(vals as string[])}
          >
            <Space direction="vertical" style={{ width: "100%" }}>
              {dlItems.map((item) => (
                <Checkbox key={item.name} value={item.name} disabled={!item.available}>
                  <Space>
                    <span>{item.label}</span>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {item.filename}
                      {item.available ? ` · ${formatBytes(item.size_bytes)}` : " · 未构建"}
                    </Typography.Text>
                  </Space>
                </Checkbox>
              ))}
            </Space>
          </Checkbox.Group>
        )}
      </Modal>

      <Modal
        title={editing ? "编辑版本" : "登记版本"}
        open={open}
        confirmLoading={saving}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            setSaving(true);
            try {
              if (editing) {
                await api(`/api/releases/${editing.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({ version: v.version }),
                });
                message.success("已保存");
              } else {
                await api("/api/releases", {
                  method: "POST",
                  body: JSON.stringify({ version: v.version, arch: v.arch }),
                });
                message.success("已登记版本");
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
          <Form.Item name="version" label="版本号" rules={[{ required: true, message: "请填写版本号" }]}>
            <Input placeholder="例如 0.1.0" autoFocus />
          </Form.Item>
          {editing ? (
            <Form.Item label="系统架构">
              <Typography.Text>{editing.arch ? `linux/${editing.arch}` : "—"}（不可修改）</Typography.Text>
            </Form.Item>
          ) : (
            <Form.Item name="arch" label="系统架构" rules={[{ required: true, message: "请选择架构" }]}>
              <Select placeholder="请选择架构" options={ARCH_OPTIONS} allowClear />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
