import { useEffect, useState } from "react";
import { Button, Modal, Space, Table, Tag, Typography, message } from "antd";
import { api } from "../api";
import { formatNo } from "../ids";
import { formatJobDateTime } from "../jobTime";
import SeamEditor from "./SeamEditor";

const LIVE = ["queued", "running", "paused", "awaiting_continue"];

type VersionRow = {
  id: string;
  label: string;
  created_at: string;
  note: string;
  change?: { summary: string; places: { where: string; name: string; image_id: number }[] };
};

export default function SeamEditModal({
  job,
  open,
  onClose,
  onChanged,
  embedded = false,
}: {
  job: any;
  open: boolean;
  onClose: () => void;
  onChanged: (job: any) => void | Promise<void>;
  embedded?: boolean;
}) {
  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [screen, setScreen] = useState<"manage" | "edit">("manage");
  const [baseLabel, setBaseLabel] = useState("当前成果");
  const [baseKey, setBaseKey] = useState(0);
  const [copying, setCopying] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const reseaming = Boolean(job) && LIVE.includes(job.status);

  useEffect(() => {
    if (!open || !job?.id) {
      setVersions([]);
      setScreen("manage");
      setCopying("");
      return;
    }
    let cancel = false;
    api
      .seamVersions(job.id)
      .then((payload) => {
        if (!cancel) setVersions(payload.versions || []);
      })
      .catch(() => {
        if (!cancel) setVersions([]);
      });
    return () => {
      cancel = true;
    };
  }, [open, job?.id, job?.status, job?.run_attempt, screen]);

  function openEditor(version: string, label: string) {
    if (!job || reseaming) return;
    Modal.confirm({
      title: `基于「${label}」拼接？`,
      content: "会把这一版完整复制一份，并在修改记录里新增一条。原来的文件不会改动。复制完成后进入拼接。",
      okText: "复制并拼接",
      cancelText: "取消",
      onOk: async () => {
        setCopying(version);
        try {
          const next = await api.seamBranch(job.id, version);
          message.success(next.message || "已复制一份，可以拼接");
          await onChanged(next);
          setBaseLabel(label);
          setBaseKey((n) => n + 1);
          setScreen("edit");
        } catch (e: any) {
          message.error(e.message || String(e));
        } finally {
          setCopying("");
        }
      },
    });
  }

  const panel = (
    <>
      {job && screen === "manage" ? (
        <>
        {reseaming ? (
          <Typography.Paragraph type="warning">正在重算正射，进入处理暂不可用。</Typography.Paragraph>
        ) : null}
        <Table
          className="seam-record-table"
          size="middle"
          pagination={false}
          rowKey="id"
          dataSource={[
            {
              id: "current",
              label: "当前成果",
              time: formatJobDateTime(job.finished_at || job.updated_at),
              summary: versions.length ? "正在使用的成果" : "还没有历史记录",
              current: true,
            },
            ...[...versions].reverse().map((row) => ({
              id: row.id,
              label: row.label,
              time: formatJobDateTime(row.created_at),
              summary: row.change?.summary || row.note || "未记录修改内容",
              current: false,
            })),
          ]}
          columns={[
            {
              title: "记录",
              dataIndex: "label",
              width: 180,
              render: (label: string, row: { current: boolean }) => (
                <Space size={8}>
                  <span>{label}</span>
                  {row.current ? <Tag color="blue">在用</Tag> : <Tag>留档</Tag>}
                </Space>
              ),
            },
            { title: "时间", dataIndex: "time", width: 180 },
            { title: "说明", dataIndex: "summary" },
            {
              title: "操作",
              width: 120,
              align: "right",
              render: (_: unknown, row: { id: string; label: string }) => (
                <Button
                  type="primary"
                  size="small"
                  disabled={reseaming}
                  loading={copying === row.id}
                  onClick={() => openEditor(row.id, row.label)}
                >
                  进入处理
                </Button>
              ),
            },
          ]}
        />
        </>
      ) : null}
      {job && screen === "edit" ? (
        <div>
          {embedded ? (
            <Button style={{ marginBottom: 8 }} onClick={() => setScreen("manage")}>
              返回修改记录
            </Button>
          ) : null}
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            正在修改「{baseLabel}」的副本。返回修改记录不会改掉已经复制的那一份。
          </Typography.Paragraph>
          <SeamEditor
            key={`${job.status}-${job.run_attempt ?? 0}-${baseKey}`}
            jobId={job.id}
            disabled={reseaming}
            onChanged={onChanged}
          />
        </div>
      ) : null}
    </>
  );
  if (!open) return null;
  if (embedded) return panel;
  return (
    <Modal
      className="seam-edit-modal"
      title={job ? `拼接线 · 任务 ${formatNo(job.seq)}` : "拼接线"}
      open={open}
      onCancel={onClose}
      footer={
        <Space>
          {screen === "edit" ? <Button onClick={() => setScreen("manage")}>返回修改记录</Button> : null}
          {reseaming ? (
            <Button
              danger
              loading={cancelling}
              onClick={() => {
                Modal.confirm({
                  title: "取消这次拼接线重算？",
                  content: "计算会停掉，成果回到上一版。刚才的修改不会写进正射。",
                  okText: "取消重算",
                  okButtonProps: { danger: true },
                  cancelText: "继续算",
                  onOk: async () => {
                    if (!job) return;
                    setCancelling(true);
                    try {
                      const next = await api.cancelReseam(job.id);
                      message.success(next.message || "已取消重算");
                      await onChanged(next);
                      setScreen("manage");
                    } catch (e: any) {
                      message.error(e.message || String(e));
                    } finally {
                      setCancelling(false);
                    }
                  },
                });
              }}
            >
              取消重算
            </Button>
          ) : null}
          <Button onClick={onClose}>关闭</Button>
        </Space>
      }
      width="96vw"
      centered
      destroyOnClose
      styles={{ body: { maxHeight: "calc(100vh - 120px)", overflow: "auto" } }}
    >
      {panel}
    </Modal>
  );
}
