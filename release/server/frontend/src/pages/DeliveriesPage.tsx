import { useEffect, useMemo, useState } from "react";
import { Button, Card, Checkbox, Form, Modal, Popconfirm, Select, Space, Table, Tooltip, Typography, message } from "antd";
import { DeleteOutlined, EyeOutlined, FolderOutlined, FileOutlined, PlusOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import { api, download } from "../api/client";
import { LIST_PAGE } from "../listPage";

const CHECKSUM_HELP = (
  <div style={{ maxWidth: 360 }}>
    <div>交付包 zip 的 SHA-256，用于核对下载文件是否完整、未被篡改。</div>
    <div style={{ marginTop: 8 }}>下载后对本机文件再算一次，应与本列完全一致：</div>
    <div style={{ marginTop: 6, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}>
      macOS / Linux：
      <br />
      shasum -a 256 交付包.zip
      <br />
      <br />
      Windows PowerShell：
      <br />
      Get-FileHash .\交付包.zip -Algorithm SHA256
    </div>
  </div>
);

type Customer = { id: number; name: string };
type Release = { id: number; version: string; arch: string; status: string };
type License = { id: number; license_id: string; customer_id: number; release_id: number; status: string };
type Delivery = {
  id: number;
  customer_id: number;
  release_id: number;
  license_id: number;
  checksum: string;
  created_at: string;
};
type DeliveryEntry = { path: string; size: number; is_dir: boolean };
type DeliveryContents = {
  delivery_id: number;
  package_name: string;
  checksum: string;
  file_count: number;
  total_size: number;
  package_path: string;
  local_dir: string;
  local_path: string;
  entries: DeliveryEntry[];
};

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function DeliveriesPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [licenses, setLicenses] = useState<License[]>([]);
  const [rows, setRows] = useState<Delivery[]>([]);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [viewLoading, setViewLoading] = useState(false);
  const [contents, setContents] = useState<DeliveryContents | null>(null);
  const [form] = Form.useForm();
  const customerId = Form.useWatch("customer_id", form);
  const releaseId = Form.useWatch("release_id", form);

  async function load() {
    const [c, r, l, d] = await Promise.all([
      api<Customer[]>("/api/customers"),
      api<Release[]>("/api/releases"),
      api<License[]>("/api/licenses"),
      api<Delivery[]>("/api/deliveries"),
    ]);
    setCustomers(c);
    setReleases(r);
    setLicenses(l);
    setRows(d);
  }

  useEffect(() => {
    load().catch((e) => message.error(String(e)));
  }, []);

  const filteredLicenses = useMemo(
    () =>
      licenses.filter(
        (l) =>
          l.status === "active" &&
          (!customerId || l.customer_id === customerId) &&
          (!releaseId || l.release_id === releaseId)
      ),
    [licenses, customerId, releaseId]
  );

  const customerName = (id: number) => customers.find((c) => c.id === id)?.name || String(id);
  const releaseVer = (id: number) => releases.find((r) => r.id === id)?.version || String(id);

  async function openContents(d: Delivery) {
    setViewOpen(true);
    setViewLoading(true);
    setContents(null);
    try {
      const data = await api<DeliveryContents>(`/api/deliveries/${d.id}/contents`);
      setContents(data);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
      setViewOpen(false);
    } finally {
      setViewLoading(false);
    }
  }

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>交付包</span>
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
            form.resetFields();
            form.setFieldsValue({ include_image_tar: true });
            setOpen(true);
          }}
        >
          生成交付包
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          { title: "客户", dataIndex: "customer_id", render: (id: number) => customerName(id) },
          { title: "版本", dataIndex: "release_id", render: (id: number) => releaseVer(id) },
          { title: "许可", dataIndex: "license_id" },
          {
            title: (
              <Space size={4}>
                <span>校验和</span>
                <Tooltip title={CHECKSUM_HELP}>
                  <QuestionCircleOutlined style={{ color: "rgba(0,0,0,0.45)", cursor: "help" }} />
                </Tooltip>
              </Space>
            ),
            dataIndex: "checksum",
            width: 280,
            render: (v: string) =>
              v ? (
                <Typography.Text
                  copyable
                  style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", wordBreak: "break-all" }}
                >
                  {v}
                </Typography.Text>
              ) : (
                "—"
              ),
          },
          {
            title: "操作",
            width: 240,
            render: (_, d) => (
              <Space wrap>
                <Button size="small" icon={<EyeOutlined />} onClick={() => openContents(d)}>
                  查看内容
                </Button>
                <Button
                  size="small"
                  type="primary"
                  onClick={() => {
                    message.info("开始下载，请在浏览器下载栏查看");
                    download(`/api/deliveries/${d.id}/download`, "");
                  }}
                >
                  下载
                </Button>
                <Popconfirm
                  title="确认删除该交付包？"
                  onConfirm={async () => {
                    try {
                      await api(`/api/deliveries/${d.id}`, { method: "DELETE" });
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
        title={contents ? `交付包内容 · ${contents.package_name}` : "交付包内容"}
        open={viewOpen}
        onCancel={() => setViewOpen(false)}
        width={820}
        footer={
          <Button type="primary" onClick={() => setViewOpen(false)}>
            关闭
          </Button>
        }
        destroyOnClose
      >
        {viewLoading || !contents ? (
          <Typography.Text type="secondary">读取 zip 目录中…</Typography.Text>
        ) : (
          <>
            <div
              style={{
                marginBottom: 14,
                padding: "10px 12px",
                background: "#f8fafc",
                border: "1px solid #e5e7eb",
                borderRadius: 8,
              }}
            >
              <Typography.Text strong>本机存放目录</Typography.Text>
              <div style={{ marginTop: 6 }}>
                <Typography.Text
                  copyable={{ text: contents.local_dir }}
                  style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
                >
                  {contents.local_dir}
                </Typography.Text>
              </div>
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  完整文件
                </Typography.Text>
                <div>
                  <Typography.Text
                    copyable={{ text: contents.local_path }}
                    style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12, wordBreak: "break-all" }}
                  >
                    {contents.local_path}
                  </Typography.Text>
                </div>
              </div>
              {contents.package_path ? (
                <div style={{ marginTop: 8 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    容器内路径（docker 卷 /artifacts）
                  </Typography.Text>
                  <div>
                    <Typography.Text
                      copyable={{ text: contents.package_path }}
                      style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12, wordBreak: "break-all" }}
                    >
                      {contents.package_path}
                    </Typography.Text>
                  </div>
                </div>
              ) : null}
            </div>
            <Space wrap style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary">
                文件 {contents.file_count} 个 · 合计 {formatBytes(contents.total_size)}
              </Typography.Text>
              <Typography.Text
                type="secondary"
                copyable={{ text: contents.checksum }}
                style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
              >
                SHA-256 {contents.checksum.slice(0, 16)}…
              </Typography.Text>
            </Space>
            <Table
              size="small"
              rowKey="path"
              pagination={false}
              scroll={{ y: 420 }}
              dataSource={contents.entries}
              columns={[
                {
                  title: "路径",
                  dataIndex: "path",
                  render: (p: string, row: DeliveryEntry) => (
                    <Space>
                      {row.is_dir ? <FolderOutlined /> : <FileOutlined />}
                      <Typography.Text
                        style={{
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                          fontSize: 12,
                          wordBreak: "break-all",
                        }}
                      >
                        {p}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: "大小",
                  dataIndex: "size",
                  width: 110,
                  render: (n: number, row: DeliveryEntry) => (row.is_dir ? "—" : formatBytes(n)),
                },
              ]}
            />
          </>
        )}
      </Modal>

      <Modal
        title="生成交付包"
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
          initialValues={{ include_image_tar: true }}
          onFinish={async (v) => {
            setSaving(true);
            try {
              await api("/api/deliveries", {
                method: "POST",
                body: JSON.stringify({
                  customer_id: v.customer_id,
                  release_id: v.release_id,
                  license_id: v.license_id,
                  include_image_tar: !!v.include_image_tar,
                }),
              });
              message.success("已生成交付包");
              setOpen(false);
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
              placeholder="已构建成功的版本"
              options={releases
                .filter((r) => r.status === "success")
                .map((r) => ({ value: r.id, label: `${r.version} (linux/${r.arch})` }))}
            />
          </Form.Item>
          <Form.Item name="license_id" label="许可" rules={[{ required: true, message: "请选择许可" }]}>
            <Select
              placeholder="有效许可"
              popupMatchSelectWidth={false}
              dropdownStyle={{ minWidth: 420 }}
              options={filteredLicenses.map((l) => ({ value: l.id, label: l.license_id }))}
              optionRender={(opt) => (
                <span className="lease-claims-full" style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                  {opt.label}
                </span>
              )}
            />
          </Form.Item>
          <Form.Item name="include_image_tar" valuePropName="checked">
            <Checkbox>包含离线全量镜像（worker/推扫 worker/backend/frontend/postgres/rabbitmq）</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
