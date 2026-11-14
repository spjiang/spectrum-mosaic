import { Table, Typography } from "antd";
import { fmtMb, type Proc } from "../processTree";

export default function JobProcessTable({ processes }: { processes: Proc[]; full?: boolean }) {
  if (!processes.length) {
    return <Typography.Text type="secondary">当前没有该任务的 Worker 进程（已结束或尚未拉起）。</Typography.Text>;
  }
  return (
    <Table
      size="small"
      rowKey="pid"
      pagination={false}
      dataSource={processes}
      expandable={{
        rowExpandable: (r) => !r.synthetic && ((r.tasks && r.tasks.length > 0) || (r.threads || 0) > 1),
        expandedRowRender: (r) =>
          r.tasks && r.tasks.length > 0 ? (
            <Table
              size="small"
              rowKey="tid"
              pagination={false}
              dataSource={r.tasks}
              columns={[
                { title: "TID", dataIndex: "tid", width: 90 },
                { title: "名称", dataIndex: "comm" },
                { title: "状态", dataIndex: "state", width: 80 },
                {
                  title: "角色",
                  dataIndex: "main",
                  width: 90,
                  render: (v: boolean) => (v ? "主线程" : "子线程"),
                },
              ]}
            />
          ) : (
            <Typography.Text type="secondary">该进程有 {r.threads} 个线程。</Typography.Text>
          ),
      }}
      columns={[
        { title: "PID", dataIndex: "pid", width: 80, render: (v: number, r: Proc) => (r.synthetic ? "—" : v) },
        {
          title: "PPID",
          dataIndex: "ppid",
          width: 80,
          render: (v: number | null, r: Proc) => (r.synthetic ? "—" : v ?? "—"),
        },
        { title: "子进程", dataIndex: "child_count", width: 80, render: (v: number) => v || 0 },
        { title: "状态", dataIndex: "state", width: 70, render: (v: string) => v || "—" },
        { title: "线程", dataIndex: "threads", width: 70, render: (v: number, r: Proc) => (r.synthetic ? "—" : v ?? "—") },
        {
          title: "RSS",
          dataIndex: "rss_mb",
          width: 90,
          render: (v: number | null, r: Proc) => (r.synthetic || v == null ? "—" : fmtMb(v)),
        },
        {
          title: "命令行",
          dataIndex: "cmdline",
          ellipsis: true,
          render: (v: string, r: Proc) => (
            <span style={{ paddingLeft: (r.depth || 0) * 16 }}>
              {(r.depth || 0) > 0 ? "↳ " : ""}
              {r.synthetic ? <Typography.Text type="secondary">{v}</Typography.Text> : v || "—"}
            </span>
          ),
        },
      ]}
    />
  );
}
