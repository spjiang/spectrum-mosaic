import { useState } from "react";
import { Button, Modal, Popover, Tooltip, message } from "antd";
import {
  CaretRightOutlined,
  DeleteOutlined,
  DownloadOutlined,
  DownOutlined,
  EditOutlined,
  EyeOutlined,
  FileSearchOutlined,
  ProfileOutlined,
  FundViewOutlined,
  PauseCircleOutlined,
  QuestionCircleOutlined,
  RedoOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { api } from "../api";
import { jobComputeState, type ComputeState } from "../processTree";

const LIVE = ["queued", "running", "paused", "awaiting_continue"];

const ACTION_OK: Record<string, string> = {
  pause: "已请求暂停，当前阶段结束后生效",
  stop: "已立即停止，页面可以继续操作",
  resume: "已继续运行",
  continue: "已继续下一阶段",
  recover: "已清理空转并从已有成果续跑",
  retry: "已下发失败重试",
};

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

export default function JobRowMenu({
  job,
  inspect,
  computeState,
  allowRun,
  hasRgb,
  onDetail,
  onParamsDetail,
  onEvaluate,
  onReport,
  onQuality,
  onParams,
  onMosaic,
  onOriginal,
  onOpen,
  onSeam,
  onChanged,
}: {
  job: { id: string; status: string; message?: string; seq?: number };
  hasRgb?: boolean;
  inspect?: any;
  computeState?: ComputeState;
  allowRun: boolean;
  onDetail: () => void;
  onParamsDetail: () => void;
  onEvaluate: () => void;
  onReport: () => void;
  onQuality: () => void;
  onParams: () => void;
  onMosaic: () => void;
  onOriginal: () => void;
  onOpen: () => void;
  onSeam?: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const state = computeState || jobComputeState(job, inspect);
  const stuck = state === "orphan" || state === "stale";
  const running = job.status === "running";
  const paused = job.status === "paused";
  const waiting = job.status === "awaiting_continue";
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled";
  const canRecover = failed || cancelled || stuck;
  const canDelete = job.status !== "running";
  const live = LIVE.includes(job.status);

  async function act(action: string) {
    if (busy) return;
    setBusy(true);
    try {
      const next = await api.jobAction(job.id, action);
      message.success(next?.message || ACTION_OK[action] || "已执行");
      await onChanged();
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  function confirmAct(title: string, content: string, action: string, danger = false) {
    setOpen(false);
    Modal.confirm({
      title,
      content,
      okText: title.replace("？", ""),
      okButtonProps: danger ? { danger: true } : undefined,
      onOk: () => act(action),
    });
  }

  function run(fn: () => void) {
    setOpen(false);
    fn();
  }

  const view: ActionItem[] = [];
  const quality: ActionItem[] = [];
  const downloads: ActionItem[] = [];
  const runningActions: ActionItem[] = [];
  if (onSeam && String(job.message || "").startsWith("拼接线处理")) {
    view.push({
      key: "seam",
      label: "拼接线",
      icon: <EditOutlined />,
      tone: "primary",
      help: "进入拼接线处理界面，调整这块成果的拼接线。",
      disabled: running,
      onClick: () => run(onSeam),
    });
  }
  view.push(
    {
      key: "detail",
      label: "任务详情",
      icon: <FileSearchOutlined />,
      tone: "primary",
      help: "查看流程、分辨率、输入输出路径和运行日志。",
      onClick: () => run(onDetail),
    },
    {
      key: "params",
      label: "参数详情",
      icon: <ProfileOutlined />,
      tone: "primary",
      help: "按处理流程查看这次运行实际保存的全部参数。后来改方案不会改变这里。",
      onClick: () => run(onParamsDetail),
    },
    {
      key: "mosaic",
      label: "拼图影像",
      icon: <EyeOutlined />,
      tone: "primary",
      help: hasRgb ? "按波段查看整幅拼图，可拖动和缩放。" : "还没有拼图成果，正射完成后才能查看。",
      disabled: !hasRgb,
      onClick: () => {
        if (!hasRgb) return;
        run(onMosaic);
      },
    },
    {
      key: "eval",
      label: "精度验收",
      icon: <FundViewOutlined />,
      tone: "primary",
      help: "在拼图上选择检测点，填写野外 GPS 坐标后评估。像素误差为拼图坐标与野外坐标的平面差。排队中的任务不能验收。",
      disabled: job.status === "queued",
      onClick: () => run(onEvaluate),
    },
  );
  quality.push(
    {
      key: "quality",
      label: "查看",
      icon: <FundViewOutlined />,
      tone: "primary",
      help: "打开质量报告。页内可看汇总，也可直接翻阅 PDF。照片定位差不是野外检测点。",
      onClick: () => run(onQuality),
    },
    {
      key: "report",
      label: "下载",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "下载质量报告 PDF。",
      onClick: () => run(onReport),
    },
  );
  downloads.push(
    {
      key: "params",
      label: "处理参数",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "下载这次任务实际使用的参数，可在处理方案里导入。",
      onClick: () => run(onParams),
    },
    {
      key: "original",
      label: "拼图原文件",
      icon: <DownloadOutlined />,
      tone: "green",
      help: hasRgb ? "下载各波段正射和 DSM 的 GeoTIFF。" : "还没有拼图原文件。",
      disabled: !hasRgb,
      onClick: () => {
        if (!hasRgb) return;
        run(onOriginal);
      },
    },
  );
  if (live) {
    runningActions.push({
      key: "open",
      label: "监控",
      icon: <EyeOutlined />,
      tone: "primary",
      help: "打开执行页，看正在跑的进度和日志。",
      onClick: () => run(onOpen),
    });
  }

  if (allowRun && running) {
    runningActions.push({
      key: "pause",
      label: "暂停",
      icon: <PauseCircleOutlined />,
      tone: "amber",
      help: stuck
        ? "包装进程已退出，会立即标为暂停。残留计算进程在继续运行或清理并续跑时清掉。"
        : "当前阶段跑完再停。暂停后可继续运行，不会新开输出目录。",
      disabled: busy,
      onClick: () =>
        confirmAct(
          "暂停该任务？",
          stuck
            ? "包装进程已退出，会立即标为暂停。残留计算进程在「继续运行」或「清理并续跑」时清掉。"
            : "当前阶段会跑完再停。暂停后可点「继续运行」，不会新开输出目录。",
          "pause",
        ),
    });
    runningActions.push({
      key: "stop",
      label: "立即停止",
      icon: <StopOutlined />,
      tone: "danger",
      help: "马上结束进程。已经写完的 DSM 和正射保留，当前阶段还没写完的部分不保留。",
      disabled: busy,
      onClick: () =>
        confirmAct(
          "立即停止计算？",
          "马上结束进程，页面恢复可操作。已经写完的 DSM 和正射保留；当前阶段还没写完的部分不保留。之后可点「继续运行」。",
          "stop",
          true,
        ),
    });
  }
  if (allowRun && paused) {
    runningActions.push({
      key: "resume",
      label: "继续运行",
      icon: <CaretRightOutlined />,
      tone: "green",
      help: "从暂停处接着跑，不新开输出目录。",
      disabled: busy,
      onClick: () => run(() => act("resume")),
    });
  }
  if (allowRun && waiting) {
    runningActions.push({
      key: "continue",
      label: "继续",
      icon: <CaretRightOutlined />,
      tone: "green",
      help: "当前阶段已结束，点此进入下一阶段。",
      disabled: busy,
      onClick: () => run(() => act("continue")),
    });
  }
  if (allowRun && failed && !stuck) {
    runningActions.push({
      key: "retry",
      label: "失败重试",
      icon: <RedoOutlined />,
      tone: "amber",
      help: "从失败处重新下发，不新开输出目录。",
      disabled: busy,
      onClick: () => run(() => act("retry")),
    });
  }
  if (allowRun && canRecover) {
    runningActions.push({
      key: "recover",
      label: "清理并续跑",
      icon: <RedoOutlined />,
      tone: "amber",
      help: "进度卡住或包装进程已退出时使用。清掉空转进程，有 DSM 则从正射接着跑。",
      disabled: busy,
      onClick: () =>
        confirmAct(
          "清理空转并续跑？",
          "用于进度卡住、包装进程已退出的情况。会清掉空转进程，有 DSM 则从正射接着跑，不新开输出目录。正常计算中请不要点。",
          "recover",
        ),
    });
  }
  const manage: ActionItem[] = [];
  if (allowRun) {
    manage.push({
      key: "delete",
      label: "删除",
      icon: <DeleteOutlined />,
      tone: "danger",
      help: canDelete ? "从列表移除这条记录，成果目录保留。" : "任务还在跑，要先暂停或立即停止，再删除。",
      disabled: !canDelete || busy,
      onClick: () => {
        if (!canDelete || busy) return;
        setOpen(false);
        Modal.confirm({
          title: "确认删除该任务？",
          content: "运行中必须先暂停或清理续跑。删除会通知 Worker 停止并从列表移除（输出目录保留）。",
          okText: "删除",
          okButtonProps: { danger: true },
          onOk: async () => {
            setBusy(true);
            try {
              await api.deleteJob(job.id);
              message.success("已删除");
              await onChanged();
            } catch (e: any) {
              message.error(e.message);
            } finally {
              setBusy(false);
            }
          },
        });
      },
    });
  }

  const groups = [
    { key: "view", title: "查看", items: view },
    { key: "quality", title: "质量报告", items: quality },
    { key: "download", title: "下载", items: downloads },
    { key: "run", title: "运行", items: runningActions },
    { key: "manage", title: "管理", items: manage },
  ].filter((group) => group.items.length > 0);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomRight"
      overlayClassName="job-action-popover"
      content={
        <div className="job-action-card">
          {groups.map((group) => (
            <section className="job-action-group" key={group.key}>
              <div className="job-action-group-title">{group.title}</div>
              <div className="job-action-group-grid">
                {group.items.map((item) => (
                  <div className={`job-action-tile is-${item.tone}`} key={item.key}>
                    <Button size="small" icon={item.icon} disabled={item.disabled} onClick={item.onClick}>
                      {item.label}
                    </Button>
                    <Tooltip title={item.help} placement="topLeft">
                      <button type="button" className="param-help-btn" aria-label={`${group.title}${item.label}说明`} onClick={(event) => event.stopPropagation()}>
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
      <Button type="link" size="small">
        操作 <DownOutlined />
      </Button>
    </Popover>
  );
}
