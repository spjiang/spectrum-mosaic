import { useEffect, useState } from "react";
import { Button, Card, Col, Form, Input, Modal, Popconfirm, Row, Space, Table, Typography, message } from "antd";
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import { LIST_PAGE } from "../listPage";

type Note = {
  id: number;
  title: string;
  body: string;
  created_at: string;
  updated_at: string;
};

function previewText(body: string, n = 80) {
  const plain = body.replace(/[#>*`_\-[\]()]/g, " ").replace(/\s+/g, " ").trim();
  if (plain.length <= n) return plain || "—";
  return `${plain.slice(0, n)}…`;
}

export default function NotesPage() {
  const [rows, setRows] = useState<Note[]>([]);
  const [open, setOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [editing, setEditing] = useState<Note | null>(null);
  const [viewing, setViewing] = useState<Note | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const bodyWatch = Form.useWatch("body", form) ?? "";

  async function load() {
    setRows(await api<Note[]>("/api/settings/notes"));
  }

  useEffect(() => {
    load().catch((e) => message.error(String(e)));
  }, []);

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ title: "", body: "" });
    setOpen(true);
  }

  function openEdit(row: Note) {
    setEditing(row);
    form.setFieldsValue({ title: row.title, body: row.body });
    setOpen(true);
  }

  function openView(row: Note) {
    setViewing(row);
    setViewOpen(true);
  }

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>注意事项</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 条 · 内部备忘 · 不同步到客户包
          </Typography.Text>
        </Space>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建注意事项
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "ID", dataIndex: "id", width: 72 },
          { title: "标题", dataIndex: "title", ellipsis: true },
          {
            title: "摘要",
            dataIndex: "body",
            ellipsis: true,
            render: (v: string) => previewText(v),
          },
          {
            title: "更新时间",
            dataIndex: "updated_at",
            width: 170,
            render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
          },
          {
            title: "操作",
            width: 220,
            render: (_, row) => (
              <Space wrap>
                <Button size="small" icon={<EyeOutlined />} onClick={() => openView(row)}>
                  查看
                </Button>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该注意事项？"
                  onConfirm={async () => {
                    try {
                      await api(`/api/settings/notes/${row.id}`, { method: "DELETE" });
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
        title={editing ? "编辑注意事项" : "新建注意事项"}
        open={open}
        confirmLoading={saving}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={920}
        destroyOnClose
        okText="保存"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            setSaving(true);
            try {
              const payload = { title: v.title, body: v.body || "" };
              if (editing) {
                await api(`/api/settings/notes/${editing.id}`, {
                  method: "PATCH",
                  body: JSON.stringify(payload),
                });
                message.success("已保存");
              } else {
                await api("/api/settings/notes", {
                  method: "POST",
                  body: JSON.stringify(payload),
                });
                message.success("已创建");
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
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请填写标题" }]}>
            <Input placeholder="例如：时钟回拨检测说明" maxLength={200} showCount />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} lg={12}>
              <Form.Item name="body" label="正文（Markdown）" style={{ marginBottom: 0 }}>
                <Input.TextArea
                  placeholder="支持 Markdown"
                  autoSize={{ minRows: 14, maxRows: 28 }}
                  style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 13 }}
                />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Typography.Text type="secondary">预览</Typography.Text>
              <article className="mosaic-manual-body" style={{ marginTop: 8, maxHeight: 420, overflow: "auto" }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{bodyWatch || "_空_"}</ReactMarkdown>
              </article>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title={viewing?.title || "查看"}
        open={viewOpen}
        onCancel={() => setViewOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setViewOpen(false)}>关闭</Button>
            <Button
              type="primary"
              icon={<EditOutlined />}
              onClick={() => {
                if (!viewing) return;
                setViewOpen(false);
                openEdit(viewing);
              }}
            >
              编辑
            </Button>
          </Space>
        }
        width={800}
        destroyOnClose
      >
        {viewing ? (
          <article className="mosaic-manual-body">
            <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
              更新于 {dayjs(viewing.updated_at).format("YYYY-MM-DD HH:mm:ss")}
            </Typography.Text>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{viewing.body || "_空_"}</ReactMarkdown>
          </article>
        ) : null}
      </Modal>
    </Card>
  );
}
