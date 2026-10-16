import { useState } from "react";
import { Button, Popconfirm, Space, Tooltip, message } from "antd";
import {
  CaretRightOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import { api } from "../api";
import { useAuth } from "../auth";
import { canExecute } from "../roles";
import { jobComputeState, type ComputeState } from "../processTree";

type JobLike = {
  id: string;
  status: string;
};

const ACTION_OK: Record<string, string> = {
  pause: "已请求暂停，当前阶段结束后生效",
  stop: "已立即停止，页面可以继续操作",
  resume: "已继续运行",
  continue: "已继续下一阶段",
  recover: "已清理空转并从已有成果续跑",
  retry: "已下发失败重试",
};

export default function JobActions({
  job,
  inspect,
  computeState,
  onChanged,
  size = "small",
}: {
  job: JobLike;
  inspect?: any;
  computeState?: ComputeState;
  onChanged?: (next?: any) => void | Promise<void>;
  size?: "small" | "middle";
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const { roles } = useAuth();
  if (!canExecute(roles)) return null;
  const compact = size === "small";
  const btnType = compact ? "link" : "default";
  const state = computeState || jobComputeState(job, inspect);
  const stuck = state === "orphan" || state === "stale";

  async function act(action: string) {
    if (busy) return;
    setBusy(action);
    try {
      const next = await api.jobAction(job.id, action);
      message.success(next?.message || ACTION_OK[action] || "已执行");
      await onChanged?.(next);
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  const running = job.status === "running";
  const paused = job.status === "paused";
  const waiting = job.status === "awaiting_continue";
  const failed = job.status === "failed";
  const cancelled = job.status === "cancelled";
  const canRecover = failed || cancelled || stuck;
  const canDelete = job.status !== "running";

  return (
    <Space wrap size={4}>
      {running && (
        <Popconfirm
          title="暂停该任务？"
          description={
            stuck
              ? "包装进程已退出，会立即标为暂停。残留计算进程在「继续运行」或「清理并续跑」时清掉。"
              : "当前阶段会跑完再停。暂停后可点「继续运行」，不会新开输出目录。"
          }
          okText="暂停"
          onConfirm={() => act("pause")}
        >
          <Button
            size={size}
            type={btnType}
            icon={<PauseCircleOutlined />}
            loading={busy === "pause"}
            disabled={Boolean(busy)}
          >
            暂停
          </Button>
        </Popconfirm>
      )}
      {running && (
        <Popconfirm
          title="立即停止计算？"
          description="马上结束进程，页面恢复可操作。已经写完的 DSM 和正射保留；当前阶段还没写完的部分不保留。之后可点「继续运行」。"
          okText="立即停止"
          okButtonProps={{ danger: true }}
          onConfirm={() => act("stop")}
        >
          <Button
            size={size}
            danger
            type={compact ? "link" : "primary"}
            icon={<PauseCircleOutlined />}
            loading={busy === "stop"}
            disabled={Boolean(busy)}
          >
            立即停止
          </Button>
        </Popconfirm>
      )}
      {paused && (
        <Button
          size={size}
          type={compact ? "link" : "primary"}
          icon={<CaretRightOutlined />}
          loading={busy === "resume"}
          disabled={Boolean(busy)}
          onClick={() => act("resume")}
        >
          继续运行
        </Button>
      )}
      {waiting && (
        <Button
          size={size}
          type={compact ? "link" : "primary"}
          icon={<CaretRightOutlined />}
          loading={busy === "continue"}
          disabled={Boolean(busy)}
          onClick={() => act("continue")}
        >
          继续
        </Button>
      )}
      {failed && !stuck && (
        <Button
          size={size}
          type={compact ? "link" : "primary"}
          icon={<RedoOutlined />}
          loading={busy === "retry"}
          disabled={Boolean(busy)}
          onClick={() => act("retry")}
        >
          失败重试
        </Button>
      )}
      {canRecover && (
        <Popconfirm
          title="清理空转并续跑？"
          description="用于进度卡住、包装进程已退出的情况。会清掉空转进程，有 DSM 则从正射接着跑，不新开输出目录。正常计算中请不要点。"
          okText="清理并续跑"
          onConfirm={() => act("recover")}
        >
          <Button
            size={size}
            type={stuck ? "primary" : btnType}
            danger={stuck}
            icon={<RedoOutlined />}
            loading={busy === "recover"}
            disabled={Boolean(busy)}
          >
            清理并续跑
          </Button>
        </Popconfirm>
      )}
      <Tooltip title={running && !stuck ? "请先暂停再删除" : "删除任务记录"}>
        <span>
          <Popconfirm
            title="确认删除该任务？"
            description="运行中必须先暂停或清理续跑。删除会通知 Worker 停止并从列表移除（输出目录保留）。"
            disabled={!canDelete || Boolean(busy)}
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={async () => {
              if (busy) return;
              setBusy("delete");
              try {
                await api.deleteJob(job.id);
                message.success("已删除");
                await onChanged?.();
              } catch (e: any) {
                message.error(e.message);
              } finally {
                setBusy(null);
              }
            }}
          >
            <Button
              size={size}
              type="link"
              danger
              icon={<DeleteOutlined />}
              disabled={!canDelete || Boolean(busy)}
            >
              删除
            </Button>
          </Popconfirm>
        </span>
      </Tooltip>
    </Space>
  );
}
