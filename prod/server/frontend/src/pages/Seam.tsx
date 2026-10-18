import { useEffect, useMemo, useState } from "react";
import { Button, Card, Empty, Modal, Select, Table, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import SeamEditor from "../components/SeamEditor";
import JobStatusTag from "../components/JobStatusTag";
import { api } from "../api";
import { formatNo } from "../ids";
import { formatJobDateTime } from "../jobTime";
import { useJobs } from "../jobsContext";
import { useAuth } from "../auth";
import { canExecute } from "../roles";

export default function SeamPage() {
  const { jobs, refresh } = useJobs();
  const { roles } = useAuth();
  const allow = canExecute(roles);
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(false);
  const [sourceId, setSourceId] = useState<string>();
  const editingId = params.get("job") || "";
  const editing = rows.find((row) => row.id === editingId) || jobs.find((row) => row.id === editingId) || null;

  const sources = useMemo(
    () =>
      [...jobs]
        .filter((item) => item.status === "succeeded" && !String(item.message || "").startsWith("拼接线处理"))
        .sort((a, b) => Number(b.seq || 0) - Number(a.seq || 0))
        .map((item) => ({ value: item.id, label: `任务 ${formatNo(item.seq)}` })),
    [jobs],
  );

  async function load() {
    setLoading(true);
    try {
      setRows(await api.seamTasks());
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function create() {
    if (!sourceId) {
      message.warning("先选择任务号");
      return;
    }
    setCreating(true);
    try {
      await api.createSeamTask(sourceId);
      message.success("已建立拼接线处理，来源成果已复制");
      setOpen(false);
      setSourceId(undefined);
      await Promise.all([load(), refresh()]);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setCreating(false);
    }
  }

  if (editingId) {
    return (
      <Card
        className="seam-page-card is-editor"
        title={`拼接线处理 ${editing ? formatNo(editing.seq) : ""}`}
        extra={<Button onClick={() => setParams({})}>返回记录</Button>}
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          {editing?.message || "正在处理这条拼接线记录"}
        </Typography.Paragraph>
        <SeamEditor
          key={editingId}
          jobId={editingId}
          disabled={editing ? ["queued", "running", "paused"].includes(editing.status) : false}
          savedEdit={Boolean(editing?.params_snapshot?.seam_edit_path)}
          onChanged={async () => {
            await Promise.all([load(), refresh()]);
          }}
        />
      </Card>
    );
  }

  return (
    <Card
      className="seam-page-card"
      title="拼接线处理记录"
      extra={
        allow ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新建拼接线处理
          </Button>
        ) : null
      }
    >
      <Table
        className="seam-record-table"
        rowKey="id"
        size="middle"
        loading={loading}
        pagination={false}
        dataSource={rows}
        locale={{ emptyText: <Empty description="还没有拼接线处理。新建一条，选择要处理的任务。" /> }}
        columns={[
          {
            title: "任务号",
            dataIndex: "seq",
            width: 110,
            render: (v: number) => <Typography.Text strong>{formatNo(v)}</Typography.Text>,
          },
          {
            title: "来源任务",
            width: 120,
            render: (_: unknown, row: any) => formatNo(row.params_snapshot?.source_seq),
          },
          {
            title: "时间",
            dataIndex: "created_at",
            width: 180,
            render: (v: string) => formatJobDateTime(v),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 110,
            render: (v: string) => <JobStatusTag status={v} />,
          },
          { title: "说明", dataIndex: "message" },
          {
            title: "操作",
            width: 120,
            align: "right",
            render: (_: unknown, row: any) => (
              <Button type="link" disabled={row.status === "running"} onClick={() => setParams({ job: row.id })}>
                处理
              </Button>
            ),
          },
        ]}
      />
      <Modal
        title="新建拼接线处理"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        onOk={create}
        onCancel={() => setOpen(false)}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary">
          选择一次已经成功的正射任务。系统会复制它的成果，生成一条新的处理记录，原来的文件保持不动。
        </Typography.Paragraph>
        <Select
          showSearch
          placeholder="选择任务号"
          style={{ width: "100%" }}
          optionFilterProp="label"
          options={sources}
          value={sourceId}
          onChange={setSourceId}
        />
      </Modal>
    </Card>
  );
}
