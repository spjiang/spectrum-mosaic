import { useState } from "react";
import { Button, Card, Form, Input, Modal, Space, Table, Typography, message } from "antd";
import { api, type PushbroomMount } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import { meters, pixels } from "../labels";

type EditValues = { name: string; effective_from: string };

export default function MountTable({
  mounts,
  builtin,
  loading,
  onChanged,
}: {
  mounts: PushbroomMount[];
  builtin: { along_offset_m: number; boresight_px: number } | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<PushbroomMount | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<EditValues>();
  const rows = [...mounts].sort((a, b) => b.effective_from.localeCompare(a.effective_from));

  async function save() {
    if (!editing) return;
    const values = await form.validateFields();
    setSaving(true);
    try {
      await api.pushbroomUpdateMount(editing.id, { name: values.name.trim(), effective_from: values.effective_from });
      message.success("已保存");
      setEditing(null);
      onChanged();
    } catch (err) {
      message.error((err as Error).message || String(err));
    } finally {
      setSaving(false);
    }
  }

  function remove(m: PushbroomMount) {
    Modal.confirm({
      title: `删除「${m.name}」？`,
      content: "已提交的任务不受影响；之后这些架次会按日期改用别的配置。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "返回",
      onOk: async () => {
        try {
          await api.pushbroomDeleteMount(m.id);
          message.success("已删除");
          onChanged();
        } catch (err) {
          message.error((err as Error).message || String(err));
        }
      },
    });
  }

  return (
    <Card className="mosaic-panel" title="安装配置">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        每个架次按飞行日期，用生效日期不晚于它的最近一组。比所有配置都早的架次用内置值
        {builtin ? `（沿航向 ${meters(builtin.along_offset_m)}，狭缝中心 ${pixels(builtin.boresight_px)}）` : ""}。
        拆装或更换相机后，在下面用新架次标定，再保存成一组新配置。
      </Typography.Paragraph>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: "还没有安装配置，所有架次都用内置值" }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "生效日期", dataIndex: "effective_from", width: 120 },
          { title: "沿航向偏移", width: 150, render: (_: unknown, m: PushbroomMount) => meters(m.along_offset_m, m.along_se_m) },
          { title: "狭缝中心偏置", width: 160, render: (_: unknown, m: PushbroomMount) => pixels(m.boresight_px, m.boresight_se_px) },
          { title: "标定架次", render: (_: unknown, m: PushbroomMount) => m.flights.join("、") || "—" },
          {
            title: "保存",
            width: 200,
            render: (_: unknown, m: PushbroomMount) => `${m.created_by || "管理员"} · ${formatJobDateTime(m.created_at).slice(0, 16)}`,
          },
          { title: "对到的架次", dataIndex: "matched", width: 100 },
          {
            title: "操作",
            width: 170,
            render: (_: unknown, m: PushbroomMount) => (
              <Space size={0}>
                <Button
                  type="link"
                  onClick={() => {
                    setEditing(m);
                    form.setFieldsValue({ name: m.name, effective_from: m.effective_from });
                  }}
                >
                  改名称和日期
                </Button>
                <Button type="link" danger onClick={() => remove(m)}>
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        open={Boolean(editing)}
        title="改名称和生效日期"
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={save}
        onCancel={() => setEditing(null)}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">两个参数值来自标定，不能改。</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="effective_from" label="生效日期" rules={[{ required: true, message: "请选择日期" }]}>
            <Input type="date" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}