import { useState } from "react";
import { Flex, Modal, Select, Typography, message } from "antd";
import { api, type PushbroomFlightItem, type PushbroomMount } from "../../api";
import { meters, pixels } from "./labels";

const AUTO = "__auto__";

/** 给单个架次手动指定安装配置，或改回按日期自动 */
export default function MountPickerModal({
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
        if (visible) {
          setValue(flight.mount.source === "manual" && flight.mount.id ? flight.mount.id : AUTO);
          if (mounts === null) load();
        }
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