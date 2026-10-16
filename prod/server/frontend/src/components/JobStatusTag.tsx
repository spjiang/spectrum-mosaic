import { Tag } from "antd";

const MAP: Record<string, { color: string; label: string }> = {
  queued: { color: "default", label: "排队" },
  running: { color: "processing", label: "运行中" },
  paused: { color: "warning", label: "已暂停" },
  awaiting_continue: { color: "gold", label: "等待继续" },
  succeeded: { color: "success", label: "成功" },
  failed: { color: "error", label: "失败" },
  cancelled: { color: "default", label: "已取消" },
};

export default function JobStatusTag({ status }: { status?: string }) {
  const meta = MAP[status || ""] || { color: "default", label: status || "-" };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}
