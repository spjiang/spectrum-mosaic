import { useState } from "react";
import { Alert, Button, Card, Flex, Input, InputNumber, Modal, Popover, Radio, Select, Typography, message } from "antd";
import { DownOutlined } from "@ant-design/icons";
import { ApiError, api, type PushbroomFlightItem, type PushbroomJob, type PushbroomMount } from "../../../api";
import MountTag from "../MountTag";
import { flightSize, flightStartText, formatBytes, formatDuration, meters, parseBandList, pixels } from "../labels";

const AUTO = "__auto__";

function MountPicker({
  flight,
  open,
  onClose,
  onDone,
}: {
  flight: PushbroomFlightItem;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [mounts, setMounts] = useState<PushbroomMount[] | null>(null);
  const [value, setValue] = useState<string>(flight.mount.source === "manual" && flight.mount.id ? flight.mount.id : AUTO);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const data = await api.pushbroomMounts();
      setMounts([...data.items].sort((a, b) => b.effective_from.localeCompare(a.effective_from)));
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  async function save() {
    setSaving(true);
    try {
      await api.pushbroomSetFlightMount(flight.name, value === AUTO ? null : value);
      message.success(value === AUTO ? "已改回按日期自动选择" : "已手动指定");
      onDone();
      onClose();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`${flight.name} 用哪组安装配置`}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      onOk={save}
      onCancel={onClose}
      afterOpenChange={(visible) => {
        if (visible && mounts === null) load();
      }}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">只影响之后提交的任务。已提交的任务保留各自的参数。</Typography.Text>
        <Select
          value={value}
          onChange={setValue}
          loading={mounts === null}
          options={[
            { value: AUTO, label: "按飞行日期自动选择" },
            ...(mounts || []).map((m) => ({
              value: m.id,
              label: `${m.name}（${m.effective_from} 起） · ${meters(m.along_offset_m)} · ${pixels(m.boresight_px)}`,
            })),
          ]}
        />
      </Flex>
    </Modal>
  );
}

export default function FlightHeader({
  flight,
  admin,
  onSubmitted,
  onMountChanged,
}: {
  flight: PushbroomFlightItem;
  admin: boolean;
  onSubmitted: (job: PushbroomJob) => void;
  onMountChanged: () => void;
}) {
  // 父组件按架次给 key，换架次时这里整体重建，选项自然回到默认
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [gsdMode, setGsdMode] = useState<"auto" | "manual">("auto");
  const [gsdCm, setGsdCm] = useState<number | null>(null);
  const [bandMode, setBandMode] = useState<"all" | "pick">("all");
  const [bandsText, setBandsText] = useState("");
  const [optionError, setOptionError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const f = flight.flight;
  const summary = `${gsdMode === "manual" && gsdCm ? `分辨率 ${gsdCm} cm` : "分辨率自动"} · ${
    bandMode === "pick" && bandsText.trim() ? `波段 ${bandsText.trim()}` : "全部波段"
  }${title.trim() ? ` · ${title.trim()}` : ""}`;

  async function submit() {
    setOptionError("");
    let bands: number[] | undefined;
    if (bandMode === "pick") {
      try {
        bands = parseBandList(bandsText, f.bands);
      } catch (err) {
        setOptionError((err as Error).message);
        setOptionsOpen(true);
        return;
      }
    }
    if (gsdMode === "manual" && !gsdCm) {
      setOptionError("请填写分辨率");
      setOptionsOpen(true);
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
      setOptionsOpen(false);
      onSubmitted(job);
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { check?: { errors?: string[] } } | undefined) : undefined;
      message.error(detail?.check?.errors?.[0] || (err as Error).message || String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const options = (
    <Flex vertical gap={12} style={{ width: 340 }}>
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
  );

  return (
    <Card className="mosaic-panel pb-wb-head">
      <Flex justify="space-between" align="flex-start" gap={16} wrap>
        <Flex vertical gap={6} style={{ minWidth: 0, flex: 1 }}>
          <Typography.Title level={4} style={{ margin: 0 }} ellipsis={{ tooltip: flight.name }}>
            {flight.name}
          </Typography.Title>
          <Typography.Text type="secondary">
            {flightStartText(f)} 起飞 · 飞行 {formatDuration(f.duration_s)} · {flightSize(f)} · {formatBytes(f.spe_bytes)}
          </Typography.Text>
          <Flex gap={8} align="center" wrap>
            <Typography.Text type="secondary">安装配置</Typography.Text>
            <MountTag mount={flight.mount} />
            <Typography.Text type="secondary">
              沿航向 {meters(flight.mount.along_offset_m)} · 狭缝中心 {pixels(flight.mount.boresight_px)}
            </Typography.Text>
            {admin && (
              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setPickerOpen(true)}>
                改选
              </Button>
            )}
          </Flex>
        </Flex>
        <Flex vertical align="flex-end" gap={6}>
          <Flex gap={8}>
            <Popover trigger="click" placement="bottomRight" open={optionsOpen} onOpenChange={setOptionsOpen} content={options}>
              <Button>
                选项 <DownOutlined />
              </Button>
            </Popover>
            <Button type="primary" loading={submitting} onClick={submit}>
              提交拼图
            </Button>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {summary}
          </Typography.Text>
        </Flex>
      </Flex>
      {flight.warnings.length > 0 && (
        <Alert style={{ marginTop: 12 }} type="warning" showIcon message={flight.warnings.join("；")} />
      )}
      {admin && (
        <MountPicker flight={flight} open={pickerOpen} onClose={() => setPickerOpen(false)} onDone={onMountChanged} />
      )}
    </Card>
  );
}