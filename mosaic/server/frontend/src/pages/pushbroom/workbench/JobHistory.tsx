import { Button, Dropdown, Empty, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { EllipsisOutlined } from "@ant-design/icons";
import type { PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import { formatJobDateTime } from "../../../jobTime";
import { PB_ACTIVE, PB_STATUS, formatDuration, jobElapsedSeconds, jobMountName } from "../labels";
import { confirmCancel, confirmDelete, retryJob } from "./jobActions";

function menuFor(job: PushbroomJob): MenuProps["items"] {
  const items: NonNullable<MenuProps["items"]> = [];
  if (job.status === "failed" || job.status === "cancelled") items.push({ key: "retry", label: "重跑" });
  if (job.status === "queued" || job.status === "running") items.push({ key: "cancel", label: "取消", danger: true });
  if (!PB_ACTIVE.includes(job.status)) items.push({ key: "delete", label: "删除", danger: true });
  return items;
}

export default function JobHistory({
  jobs,
  selectedId,
  onSelect,
  onChanged,
}: {
  jobs: PushbroomJob[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  if (!jobs.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有任务" />;
  return (
    <div className="pb-history">
      {jobs.map((job) => {
        const s = PB_STATUS[job.status];
        const items = menuFor(job);
        return (
          <div
            key={job.id}
            className={`pb-history-item${job.id === selectedId ? " is-active" : ""}`}
            role="button"
            tabIndex={0}
            aria-pressed={job.id === selectedId}
            onClick={() => onSelect(job.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(job.id);
              }
            }}
          >
            <div className="pb-history-row">
              <Typography.Text strong>{formatNo(job.seq)}</Typography.Text>
              <Tag color={s.color}>{s.text}</Tag>
              {items && items.length > 0 && (
                <Dropdown
                  trigger={["click"]}
                  menu={{
                    items,
                    onClick: ({ key, domEvent }) => {
                      domEvent.stopPropagation();
                      if (key === "retry") retryJob(job, onChanged);
                      if (key === "cancel") confirmCancel(job, onChanged);
                      if (key === "delete") confirmDelete(job, onChanged);
                    },
                  }}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={<EllipsisOutlined />}
                    aria-label={`任务 ${formatNo(job.seq)} 的操作`}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Dropdown>
              )}
            </div>
            <Typography.Text type="secondary" className="pb-history-meta">
              {formatJobDateTime(job.created_at).slice(0, 16)} · {formatDuration(jobElapsedSeconds(job))}
            </Typography.Text>
            <Typography.Text type="secondary" className="pb-history-meta">
              {jobMountName(job.params)}
              {job.run_attempt > 1 ? ` · 第 ${job.run_attempt} 次运行` : ""}
            </Typography.Text>
          </div>
        );
      })}
    </div>
  );
}