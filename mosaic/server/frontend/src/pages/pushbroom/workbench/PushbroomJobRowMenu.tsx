import { useState } from "react";
import { Button, Popover, Tooltip, message } from "antd";
import {
  DeleteOutlined,
  DownOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  PauseCircleOutlined,
  QuestionCircleOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { api, type PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";
import { PB_ACTIVE } from "../labels";
import { confirmCancel, confirmDelete, retryJob } from "./jobActions";

type ActionTone = "primary" | "green" | "amber" | "danger";

type ActionItem = {
  key: string;
  label: string;
  help: string;
  icon: ReactNode;
  tone: ActionTone;
  disabled?: boolean;
  onClick: () => void;
};

/** 推扫任务操作列：分组弹窗 */
export default function PushbroomJobRowMenu({
  job,
  onDetail,
  onPreview,
  onDetailFiles,
  onChanged,
}: {
  job: PushbroomJob;
  onDetail: () => void;
  onPreview: () => void;
  onDetailFiles: () => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [packing, setPacking] = useState(false);
  const live = PB_ACTIVE.includes(job.status);
  const succeeded = job.status === "succeeded";
  const canRetry = job.status === "failed" || job.status === "cancelled";
  const canCancel = job.status === "queued" || job.status === "running";
  const canDelete = !PB_ACTIVE.includes(job.status);

  function run(fn: () => void) {
    setOpen(false);
    fn();
  }

  async function pack() {
    setOpen(false);
    setPacking(true);
    try {
      await api.pushbroomDownloadArchive(job.id);
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setPacking(false);
    }
  }

  const view: ActionItem[] = [
    {
      key: "detail",
      label: "任务详情",
      icon: <FileSearchOutlined />,
      tone: "primary",
      help: "处理参数、各步耗时、运行日志。",
      onClick: () => run(onDetail),
    },
    {
      key: "preview",
      label: live ? "运行进度" : "结果预览",
      icon: <EyeOutlined />,
      tone: "primary",
      help: live ? "当前阶段、总进度，可取消排队或运行中的任务。" : "快视图预览、全屏查看与打包下载。",
      disabled: !live && job.status !== "succeeded" && job.status !== "failed" && job.status !== "cancelled",
      onClick: () => run(onPreview),
    },
  ];

  const downloads: ActionItem[] = [
    {
      key: "archive",
      label: "打包下载",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "下载本次成果目录的 zip 包。",
      disabled: !succeeded || packing,
      onClick: () => pack(),
    },
    {
      key: "files",
      label: "成果文件",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "在任务详情里按文件逐个下载 GeoTIFF、报告等。",
      disabled: !succeeded,
      onClick: () => run(onDetailFiles),
    },
  ];

  const manage: ActionItem[] = [];
  if (canCancel) {
    manage.push({
      key: "cancel",
      label: "取消",
      icon: <PauseCircleOutlined />,
      tone: "danger",
      help: "停止排队或正在计算的任务。",
      onClick: () => run(() => confirmCancel(job, onChanged)),
    });
  }
  if (canRetry) {
    manage.push({
      key: "retry",
      label: "重跑",
      icon: <RedoOutlined />,
      tone: "amber",
      help: "用相同参数再提交一次。",
      onClick: () => run(() => retryJob(job, onChanged)),
    });
  }
  if (canDelete) {
    manage.push({
      key: "delete",
      label: "删除",
      icon: <DeleteOutlined />,
      tone: "danger",
      help: "从列表移除记录，成果目录保留。",
      onClick: () =>
        run(() => {
          confirmDelete(job, onChanged);
        }),
    });
  }

  const groups = [
    { key: "view", title: "查看", items: view },
    { key: "download", title: "下载", items: downloads },
    { key: "manage", title: "管理", items: manage },
  ].filter((g) => g.items.length > 0);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomRight"
      getPopupContainer={() => document.body}
      overlayClassName="job-action-popover"
      content={
        <div className="job-action-card">
          {groups.map((group) => (
            <section className="job-action-group" key={group.key}>
              <div className="job-action-group-title">{group.title}</div>
              <div className="job-action-group-grid">
                {group.items.map((item) => (
                  <div className={`job-action-tile is-${item.tone}`} key={item.key}>
                    <Button size="small" icon={item.icon} disabled={item.disabled} loading={item.key === "archive" && packing} onClick={item.onClick}>
                      {item.label}
                    </Button>
                    <Tooltip title={item.help} placement="topLeft">
                      <button type="button" className="param-help-btn" aria-label={`${group.title}${item.label}说明`} onClick={(e) => e.stopPropagation()}>
                        <QuestionCircleOutlined />
                      </button>
                    </Tooltip>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      }
    >
      <Button type="link" size="small" aria-label={`任务 ${formatNo(job.seq)} 操作`}>
        操作 <DownOutlined />
      </Button>
    </Popover>
  );
}