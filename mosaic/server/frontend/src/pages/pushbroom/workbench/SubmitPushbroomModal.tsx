import { useState } from "react";
import { Flex, Input, InputNumber, Modal, Radio, Typography, message } from "antd";
import { ApiError, api, type PushbroomFlightItem, type PushbroomJob } from "../../../api";
import { parseBandList } from "../labels";

/** 提交推扫拼图：从架次操作菜单打开 */
export default function SubmitPushbroomModal({
  flight,
  open,
  onClose,
  onSubmitted,
}: {
  flight: PushbroomFlightItem;
  open: boolean;
  onClose: () => void;
  onSubmitted: (job: PushbroomJob) => void;
}) {
  const [title, setTitle] = useState("");
  const [gsdMode, setGsdMode] = useState<"auto" | "manual">("auto");
  const [gsdCm, setGsdCm] = useState<number | null>(null);
  const [bandMode, setBandMode] = useState<"all" | "pick">("all");
  const [bandsText, setBandsText] = useState("");
  const [optionError, setOptionError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const f = flight.flight;

  async function submit() {
    setOptionError("");
    let bands: number[] | undefined;
    if (bandMode === "pick") {
      try {
        bands = parseBandList(bandsText, f.bands);
      } catch (err) {
        setOptionError((err as Error).message);
        return;
      }
    }
    if (gsdMode === "manual" && !gsdCm) {
      setOptionError("请填写分辨率");
      return;
    }
    setSubmitting(true);
    try {
      const job = await api.pushbroomCreate({
        input_dir: flight.input_dir,
        title: title.trim() || undefined,
        params: {
          ...(gsdMode === "manual" && gsdCm ? { gsd_m: gsdCm / 100 } : {}),
          ...(bands ? { bands } : {}),
        },
      });
      message.success(`推扫任务 #${job.seq} 已提交`);
      onClose();
      onSubmitted(job);
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { check?: { errors?: string[] } } | undefined) : undefined;
      message.error(detail?.check?.errors?.[0] || (err as Error).message || String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title={`提交拼图 · ${flight.name}`}
      open={open}
      onCancel={onClose}
      okText="提交"
      confirmLoading={submitting}
      onOk={submit}
      destroyOnClose
      width={480}
    >
      <Flex vertical gap={12}>
        <div>
          <Typography.Text>任务名称</Typography.Text>
          <Input maxLength={120} placeholder={`不填用 ${flight.name}`} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Typography.Text>成果分辨率</Typography.Text>
          <Radio.Group value={gsdMode} onChange={(e) => setGsdMode(e.target.value)} style={{ display: "block" }}>
            <Radio value="auto">自动（按航高和相机估算）</Radio>
            <Radio value="manual">指定</Radio>
          </Radio.Group>
          {gsdMode === "manual" && (
            <InputNumber min={1} max={200} step={0.5} suffix="cm" value={gsdCm} onChange={setGsdCm} style={{ width: 160, marginTop: 6 }} />
          )}
        </div>
        <div>
          <Typography.Text>输出波段</Typography.Text>
          <Radio.Group value={bandMode} onChange={(e) => setBandMode(e.target.value)} style={{ display: "block" }}>
            <Radio value="all">全部波段</Radio>
            <Radio value="pick">指定波段</Radio>
          </Radio.Group>
          {bandMode === "pick" && (
            <Input
              style={{ marginTop: 6 }}
              placeholder={`例如 48, 150, 208${f.bands ? `（共 ${f.bands} 个）` : ""}`}
              value={bandsText}
              onChange={(e) => setBandsText(e.target.value)}
            />
          )}
        </div>
        {optionError && <Typography.Text type="danger">{optionError}</Typography.Text>}
      </Flex>
    </Modal>
  );
}