import { useState } from "react";
import { Button, Modal, Popover, Spin, Tooltip, message } from "antd";
import {
  CaretRightOutlined,
  DeleteOutlined,
  DownloadOutlined,
  DownOutlined,
  EditOutlined,
  EyeOutlined,
  FileSearchOutlined,
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
  onEvaluate,
  onReport,
  onParams,
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
  onEvaluate: () => void;
  onReport: () => void;
  onParams: () => void;
  onOpen: () => void;
  onSeam?: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rgbOpen, setRgbOpen] = useState(false);
  const [rgbUrl, setRgbUrl] = useState("");
  const [rgbLoading, setRgbLoading] = useState(false);
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

  async function openRgb() {
    setOpen(false);
    setRgbOpen(true);
    setRgbLoading(true);
    try {
      const blob = await api.rgbPreview(job.id);
      const url = URL.createObjectURL(blob);
      setRgbUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    } catch (e: any) {
      message.error(e.message || "RGB 拼图读取失败");
      setRgbOpen(false);
    } finally {
      setRgbLoading(false);
    }
  }

  function closeRgb() {
    setRgbOpen(false);
    setRgbUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return "";
    });
  }

  function downloadRgb() {
    if (!rgbUrl) return;
    const a = document.createElement("a");
    a.href = rgbUrl;
    a.download = job.seq != null ? `任务${job.seq}_RGB拼图.png` : "RGB拼图影像.png";
    a.click();
  }

  const items: ActionItem[] = [];
  if (onSeam && String(job.message || "").startsWith("拼接线处理")) {
    items.push({
      key: "seam",
      label: "拼接线处理",
      icon: <EditOutlined />,
      tone: "primary",
      help: "进入拼接线处理界面，调整这块成果的拼接线。",
      disabled: running,
      onClick: () => run(onSeam),
    });
  }
  items.push(
    {
      key: "detail",
      label: "任务详情",
      icon: <FileSearchOutlined />,
      tone: "primary",
      help: "查看流程、分辨率、输入输出路径和运行日志。",
      onClick: () => run(onDetail),
    },
    {
      key: "eval",
      label: "评估",
      icon: <FundViewOutlined />,
      tone: "primary",
      help: "和外方位对应的 GPS 比平面偏差，按拼图像素计，标准 2 px。排队中的任务还不能评估。",
      disabled: job.status === "queued",
      onClick: () => run(onEvaluate),
    },
    {
      key: "report",
      label: "下载报告",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "下载质量报告 PDF。",
      onClick: () => run(onReport),
    },
    {
      key: "params",
      label: "下载参数",
      icon: <DownloadOutlined />,
      tone: "green",
      help: "下载这次任务实际使用的参数，可在处理方案里导入。",
      onClick: () => run(onParams),
    },
    {
      key: "rgb",
      label: "查看RGB影像",
      icon: <EyeOutlined />,
      tone: "primary",
      help: hasRgb ? "打开这块成果的 RGB 彩色拼图。" : "还没有 RGB 拼图影像，正射完成后才能查看。",
      disabled: !hasRgb,
      onClick: () => {
        if (!hasRgb) return;
        void openRgb();
      },
    },
  );
  if (live) {
    items.push({
      key: "open",
      label: "监控",
      icon: <EyeOutlined />,
      tone: "primary",
      help: "打开执行页，看正在跑的进度和日志。",
      onClick: () => run(onOpen),
    });
  }

  if (allowRun && running) {
    items.push({
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
    items.push({
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
    items.push({
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
    items.push({
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
    items.push({
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
    items.push({
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
  if (allowRun) {
    items.push({
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

  return (
    <>
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomRight"
      overlayClassName="job-action-popover"
      content={
        <div className="job-action-card">
          {items.map((item) => (
            <div className={`job-action-tile is-${item.tone}`} key={item.key}>
              <Button
                size="small"
                icon={item.icon}
                disabled={item.disabled}
                onClick={item.onClick}
              >
                {item.label}
              </Button>
              <Tooltip title={item.help} placement="topLeft">
                <button type="button" className="param-help-btn" aria-label={`${item.label}说明`} onClick={(e) => e.stopPropagation()}>
                  <QuestionCircleOutlined />
                </button>
              </Tooltip>
            </div>
          ))}
        </div>
      }
    >
      <Button type="link" size="small">
        操作 <DownOutlined />
      </Button>
    </Popover>
    <Modal
      title="RGB 拼图影像"
      open={rgbOpen}
      width={880}
      destroyOnClose
      onCancel={closeRgb}
      footer={
        <Button type="primary" icon={<DownloadOutlined />} disabled={!rgbUrl || rgbLoading} onClick={downloadRgb}>
          下载
        </Button>
      }
    >
      <div className="seam-photo-modal">
        {rgbLoading ? <Spin /> : null}
        {rgbUrl ? <img src={rgbUrl} alt="RGB 拼图影像" /> : null}
      </div>
    </Modal>
    </>
  );
}
