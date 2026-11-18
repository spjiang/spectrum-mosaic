import { useEffect, useState } from "react";
import { Button, Card, Descriptions, Modal, Table, Tag, Typography, message } from "antd";
import type { TablePaginationConfig } from "antd";
import dayjs from "dayjs";
import { api } from "../api/client";

type Claim = {
  id: number;
  license_id: string;
  customer_name: string;
  machine_id: string;
  ok: boolean;
  detail: string;
  lease_expires_at: string | null;
  client_ip: string;
  created_at: string;
};

type Page = {
  total: number;
  page: number;
  page_size: number;
  items: Claim[];
};

type Customer = {
  id: number;
  name: string;
  contact: string;
  status: string;
  notes: string;
  created_at: string;
  updated_at: string;
};

type Release = {
  id: number;
  version: string;
  arch: string;
  status: string;
};

type License = {
  id: number;
  license_id: string;
  customer_id: number;
  release_id: number;
  starts_at: string;
  expires_at: string;
  issued_at: string;
  file_path: string;
  file_checksum: string;
  status: string;
  machine_id: string;
  created_at: string;
};

const customerStatus: Record<string, { color: string; label: string }> = {
  trial: { color: "processing", label: "试用" },
  formal: { color: "success", label: "正式" },
  disabled: { color: "default", label: "停用" },
};

function when(value: string | null | undefined) {
  if (!value) return "—";
  const d = dayjs(value);
  return d.isValid() ? d.format("YYYY-MM-DD HH:mm:ss") : value;
}

function fmt(value: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function fullText(value: string) {
  if (!value) return "—";
  return <span className="lease-claims-full">{value}</span>;
}

export default function LeaseClaimsPage() {
  const [rows, setRows] = useState<Claim[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [loading, setLoading] = useState(false);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [licenses, setLicenses] = useState<License[]>([]);
  const [customerOpen, setCustomerOpen] = useState<Customer | null>(null);
  const [licenseOpen, setLicenseOpen] = useState<License | null>(null);
  const [missing, setMissing] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<Customer[]>("/api/customers"),
      api<Release[]>("/api/releases"),
      api<License[]>("/api/licenses"),
    ])
      .then(([c, r, l]) => {
        setCustomers(c);
        setReleases(r);
        setLicenses(l);
      })
      .catch((e) => message.error(String(e)));
  }, []);

  function openCustomer(row: Claim) {
    const license = licenses.find((item) => item.license_id === row.license_id);
    const customer =
      (license && customers.find((item) => item.id === license.customer_id)) ||
      customers.find((item) => item.name === row.customer_name);
    if (!customer) {
      setMissing(row.customer_name ? `客户「${row.customer_name}」已不在档案中` : "这条记录没有客户");
      return;
    }
    setCustomerOpen(customer);
  }

  function openLicense(row: Claim) {
    const license = licenses.find((item) => item.license_id === row.license_id);
    if (!license) {
      setMissing(row.license_id ? `许可 ${row.license_id} 已不在签发列表中` : "这条记录没有许可");
      return;
    }
    setLicenseOpen(license);
  }

  useEffect(() => {
    setLoading(true);
    api<Page>(`/api/licenses/lease-claims?page=${page}&page_size=${pageSize}`)
      .then((data) => {
        setRows(data.items);
        setTotal(data.total);
      })
      .catch((e) => message.error(String(e)))
      .finally(() => setLoading(false));
  }, [page, pageSize]);

  const pagination: TablePaginationConfig = {
    current: page,
    pageSize,
    total,
    hideOnSinglePage: false,
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (n) => `共 ${n} 条`,
    onChange: (next, size) => {
      setPage(size !== pageSize ? 1 : next);
      setPageSize(size);
    },
  };

  return (
    <Card
      className="mosaic-panel"
      title={
        <span>
          租约日志
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13, marginLeft: 12 }}>
            客户 worker 向发版台领取租约的记录，成功和拒绝都会留下
          </Typography.Text>
        </span>
      }
    >
      <Table
        className="lease-claims-table"
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={pagination}
        tableLayout="auto"
        columns={[
          { title: "时间", dataIndex: "created_at", render: (v: string) => fmt(v) },
          {
            title: "结果",
            dataIndex: "ok",
            render: (ok: boolean) => (ok ? <Tag color="green">成功</Tag> : <Tag color="red">拒绝</Tag>),
          },
          { title: "说明", dataIndex: "detail", render: (v: string) => fullText(v) },
          {
            title: "客户",
            dataIndex: "customer_name",
            render: (v: string, row: Claim) =>
              v ? (
                <Button type="link" className="lease-claims-link" onClick={() => openCustomer(row)}>
                  {v}
                </Button>
              ) : (
                "—"
              ),
          },
          {
            title: "许可",
            dataIndex: "license_id",
            render: (v: string, row: Claim) =>
              v ? (
                <span className="lease-claims-full">
                  <Button type="link" className="lease-claims-link" onClick={() => openLicense(row)}>
                    {v}
                  </Button>
                  <Typography.Text copyable={{ text: v }} />
                </span>
              ) : (
                "—"
              ),
          },
          {
            title: "机器指纹",
            dataIndex: "machine_id",
            render: (v: string) =>
              v ? <Typography.Text copyable={{ text: v }}>{fullText(v)}</Typography.Text> : "—",
          },
          {
            title: "租约到期",
            dataIndex: "lease_expires_at",
            render: (v: string | null) => fmt(v),
          },
          { title: "来源地址", dataIndex: "client_ip", render: (v: string) => fullText(v) },
        ]}
      />

      <Modal
        title="客户详情"
        open={customerOpen !== null}
        footer={null}
        onCancel={() => setCustomerOpen(null)}
        destroyOnClose
        width={640}
      >
        {customerOpen && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="单位">{customerOpen.name}</Descriptions.Item>
            <Descriptions.Item label="联系人">{customerOpen.contact || "—"}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={(customerStatus[customerOpen.status] || { color: "default" }).color}>
                {(customerStatus[customerOpen.status] || { label: customerOpen.status }).label}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="备注">{customerOpen.notes || "—"}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{when(customerOpen.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{when(customerOpen.updated_at)}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      <Modal
        title="许可详情"
        open={licenseOpen !== null}
        footer={null}
        onCancel={() => setLicenseOpen(null)}
        destroyOnClose
        width={720}
      >
        {licenseOpen && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="许可 ID">
              <Typography.Text copyable>{licenseOpen.license_id}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="客户">
              {customers.find((item) => item.id === licenseOpen.customer_id)?.name || licenseOpen.customer_id}
            </Descriptions.Item>
            <Descriptions.Item label="版本">
              {(() => {
                const release = releases.find((item) => item.id === licenseOpen.release_id);
                return release ? `${release.version} · ${release.arch}` : licenseOpen.release_id;
              })()}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={licenseOpen.status === "active" ? "success" : "default"}>
                {licenseOpen.status === "active" ? "有效" : "停用"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="机器指纹">
              {licenseOpen.machine_id ? (
                <Typography.Text copyable className="lease-claims-full">
                  {licenseOpen.machine_id}
                </Typography.Text>
              ) : (
                "未绑定"
              )}
            </Descriptions.Item>
            <Descriptions.Item label="生效时间">{when(licenseOpen.starts_at)}</Descriptions.Item>
            <Descriptions.Item label="到期时间">{when(licenseOpen.expires_at)}</Descriptions.Item>
            <Descriptions.Item label="签发时间">{when(licenseOpen.issued_at)}</Descriptions.Item>
            <Descriptions.Item label="文件">{licenseOpen.file_path || "—"}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      <Modal title="记录不存在" open={missing !== null} footer={null} onCancel={() => setMissing(null)}>
        {missing}
      </Modal>
    </Card>
  );
}
