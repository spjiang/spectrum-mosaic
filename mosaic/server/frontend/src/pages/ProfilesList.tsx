import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Card, Empty, Input, Space, Table, Tag, Typography, message } from "antd";
import { PlusOutlined, ReloadOutlined, EditOutlined, DownloadOutlined, UploadOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { presetLabel } from "../presets";
import { useAuth } from "../auth";
import { canConfigure } from "../roles";
import { ResizableHeader, useColumnWidths } from "../components/ResizableHeader";
import {
  downloadJson,
  parseProfileFile,
  profileFilename,
  serializeProfile,
  uniqueProfileName,
} from "../profileExport";

const COL_W = {
  id: 96,
  name: 280,
  version: 88,
  preset: 140,
  bands: 240,
  debug: 100,
  actions: 180,
};

export default function ProfilesListPage() {
  const { roles } = useAuth();
  const allowEdit = canConfigure(roles);
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [keyword, setKeyword] = useState("");
  const nav = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const { widths, setWidth, total } = useColumnWidths("profiles-table", COL_W);

  async function reload() {
    setLoading(true);
    try {
      const list = await api.profiles();
      setRows(Array.isArray(list) ? list : []);
    } catch (e) {
      message.error(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload().catch((e) => {
      setLoading(false);
      message.error(String(e));
    });
  }, []);

  const filtered = useMemo(() => {
    if (!keyword) return rows;
    const k = keyword.toLowerCase();
    return rows.filter((r) => `${r.id} ${r.name} ${r.description || ""}`.toLowerCase().includes(k));
  }, [rows, keyword]);

  function downloadOne(p: any) {
    downloadJson(profileFilename(p), serializeProfile(p));
    message.success(`已下载 ${p.name}`);
  }

  async function onImportFile(file: File) {
    setImporting(true);
    try {
      const parsed = parseProfileFile(await file.text());
      const name = uniqueProfileName(
        parsed.name,
        rows.map((r) => String(r.name)),
      );
      await api.createProfile({
        name,
        description: parsed.description,
        preset: parsed.preset,
        values: parsed.values,
      });
      message.success(name === parsed.name ? `已导入 ${name}` : `已导入为 ${name}（原名已存在）`);
      await reload();
    } catch (e: any) {
      message.error(e.message || String(e));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const col = (key: keyof typeof COL_W, spec: Record<string, unknown>) => ({
    ...spec,
    width: widths[key],
    onHeaderCell: () => ({ width: widths[key], onResize: (w: number) => setWidth(key, w) }),
  });

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>处理方案</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 套
          </Typography.Text>
        </Space>
      }
      extra={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="方案号 / 名称 / 说明"
            style={{ width: 240 }}
            onSearch={setKeyword}
            onChange={(e) => !e.target.value && setKeyword("")}
          />
          <Button icon={<ReloadOutlined />} onClick={() => reload().catch((e) => message.error(String(e)))}>
            刷新
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onImportFile(f);
            }}
          />
          {allowEdit && (
            <>
          <Button icon={<UploadOutlined />} loading={importing} onClick={() => fileRef.current?.click()}>
            导入方案
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => nav("/profiles/new")}>
            新建方案
          </Button>
            </>
          )}
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={filtered}
        tableLayout="fixed"
        scroll={{ x: total }}
        components={{ header: { cell: ResizableHeader } }}
        locale={{
          emptyText: (
            <Empty
              description={
                <span>
                  暂无处理方案。请点右上角新建或导入。
                </span>
              }
            />
          ),
        }}
        pagination={{ pageSize: 12, showSizeChanger: false, showTotal: (n) => `共 ${n} 条` }}
        columns={[
          col("id", {
            title: "方案号",
            dataIndex: "id",
            sorter: (a: any, b: any) => Number(a.id) - Number(b.id),
            defaultSortOrder: "ascend",
            render: (v: number) => <Typography.Text strong>#{v}</Typography.Text>,
          }),
          col("name", {
            title: "方案名称",
            dataIndex: "name",
            ellipsis: true,
            render: (v: string, r: any) => (
              <div>
                <Space size={6}>
                  <Typography.Text strong>{v}</Typography.Text>
                  {String(v).includes("测区示例") && <Tag color="blue">示例</Tag>}
                </Space>
                {r.description && (
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                      {r.description}
                    </Typography.Text>
                  </div>
                )}
              </div>
            ),
          }),
          col("version", {
            title: "版本",
            dataIndex: "version",
            render: (v: number) => `v${v}`,
          }),
          col("preset", {
            title: "预设",
            dataIndex: "preset",
            render: (v: string | null) => (v ? <Tag color="blue">{presetLabel(v)}</Tag> : <Tag>{presetLabel(null)}</Tag>),
          }),
          col("bands", {
            title: "波段",
            ellipsis: true,
            render: (_: unknown, r: any) => {
              const bands = r.values?.bands;
              if (!bands) return "—";
              const text = Array.isArray(bands) ? bands.join(", ") : String(bands);
              return (
                <Typography.Text ellipsis title={text}>
                  {text}
                </Typography.Text>
              );
            },
          }),
          col("debug", {
            title: "调试",
            render: (_: unknown, r: any) =>
              r.values?.benchmark_dir ? <Tag color="blue">调试</Tag> : <Tag>无</Tag>,
          }),
          col("actions", {
            title: "操作",
            render: (_: unknown, r: any) => (
              <Space size={0}>
                <Button type="link" icon={<EditOutlined />} onClick={() => nav(`/profiles/${r.id}/edit`)}>
                  {allowEdit ? "编辑" : "查看"}
                </Button>
                <Button type="link" icon={<DownloadOutlined />} onClick={() => downloadOne(r)}>
                  下载
                </Button>
              </Space>
            ),
          }),
        ]}
      />
    </Card>
  );
}
