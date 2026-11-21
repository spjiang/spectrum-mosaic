import { Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import { formatJobDateTime } from "../../../jobTime";
import { PB_STATUS, formatDuration, jobElapsedSeconds, jobMountName } from "../labels";
import { mountsTablePagination } from "../mounts/listTools";
import PushbroomJobRowMenu from "./PushbroomJobRowMenu";

/** 历次任务：表格 + 分页，操作列弹窗 */
export default function JobHistory({
  jobs,
  onDetail,
  onPreview,
  onDetailFiles,
  onChanged,
}: {
  jobs: PushbroomJob[];
  onDetail: (jobId: string) => void;
  onPreview: (jobId: string) => void;
  onDetailFiles: (jobId: string) => void;
  onChanged: () => void;
}) {
  const columns: ColumnsType<PushbroomJob> = [
    {
      title: "编号",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, job) => formatNo(job.seq),
    },
    {
      title: "状态",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, job) => {
        const s = PB_STATUS[job.status];
        return <Tag color={s.color}>{s.text}</Tag>;
      },
    },
    {
      title: "提交时间",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, job) => formatJobDateTime(job.created_at).slice(0, 16),
    },
    {
      title: "耗时",
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, job) => formatDuration(jobElapsedSeconds(job)),
    },
    {
      title: "安装配置",
      ellipsis: true,
      render: (_: unknown, job) => jobMountName(job.params),
    },
    {
      title: "操作",
      onCell: () => ({ className: "jobs-actions-cell", style: { whiteSpace: "nowrap" } }),
      render: (_: unknown, job) => (
        <PushbroomJobRowMenu
          job={job}
          onDetail={() => onDetail(job.id)}
          onPreview={() => onPreview(job.id)}
          onDetailFiles={() => onDetailFiles(job.id)}
          onChanged={onChanged}
        />
      ),
    },
  ];

  return (
    <div className="pb-mounts-panel">
      <Table
        className="pb-mounts-table"
        rowKey="id"
        size="small"
        dataSource={jobs}
        columns={columns}
        pagination={mountsTablePagination(jobs.length)}
        tableLayout="auto"
        locale={{
          emptyText: (
            <Typography.Text type="secondary">这个架次还没有任务，在架次列表里点「操作 → 提交拼图」</Typography.Text>
          ),
        }}
        onRow={(job) => ({
          onDoubleClick: (e) => {
            const el = e.target as HTMLElement;
            if (el.closest("button, a, .ant-popover, .job-action-popover")) return;
            onPreview(job.id);
          },
        })}
      />
    </div>
  );
}