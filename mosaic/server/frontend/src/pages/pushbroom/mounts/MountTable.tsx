import { useMemo, useState } from "react";
import { Button, Flex, Form, Input, Modal, Space, Table, Typography, message } from "antd";
import { api, type PushbroomMount } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import { meters, pixels } from "../labels";
import { matchMountKeyword, mountsTablePagination } from "./listTools";

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
  const [keyword, setKeyword] = useState("");
  const [fromDay, setFromDay] = useState("");
  const [toDay, setToDay] = useState("");

  const rows = useMemo(() => {
    const sorted = [...mounts].sort((a, b) => b.effective_from.localeCompare(a.effective_from));
    return sorted.filter((m) => {
      if (!matchMountKeyword(m.name, keyword) && !matchMountKeyword(m.flights.join(" "), keyword)) return false;
      if (fromDay && m.effective_from < fromDay) return false;
      if (toDay && m.effective_from > toDay) return false;
      return true;
    });
  }, [fromDay, keyword, mounts, toDay]);

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
    <div className="pb-mounts-panel">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 12 }}>
        配置保存在数据库。没有匹配到任何配置时，使用内置值
        {builtin ? `（沿航向 ${meters(builtin.along_offset_m)}，狭缝中心 ${pixels(builtin.boresight_px)}）` : ""}
        。新建请打开「新建配置」页签。
      </Typography.Paragraph>
      <Flex className="pb-mounts-toolbar" gap={8} wrap align="center">
        <Input
          allowClear
          placeholder="搜索名称或标定架次"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 220 }}
        />
        <Typography.Text type="secondary">生效日期</Typography.Text>
        <Input type="date" value={fromDay} onChange={(e) => setFromDay(e.target.value)} style={{ width: 150 }} />
        <Typography.Text type="secondary">至</Typography.Text>
        <Input type="date" value={toDay} onChange={(e) => setToDay(e.target.value)} style={{ width: 150 }} />
        {(keyword || fromDay || toDay) && (
          <Button
            type="link"
            onClick={() => {
              setKeyword("");
              setFromDay("");
              setToDay("");
            }}
          >
            清空筛选
          </Button>
        )}
      </Flex>
      <Table
        className="pb-mounts-table"
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={mountsTablePagination(rows.length)}
        tableLayout="auto"
        locale={{ emptyText: mounts.length ? "没有符合筛选条件的配置" : "还没有安装配置" }}
        columns={[
          { title: "名称", dataIndex: "name", ellipsis: true },
          { title: "生效日期", dataIndex: "effective_from" },
          {
            title: "安装参数",
            render: (_: unknown, m: PushbroomMount) =>
              `${meters(m.along_offset_m, m.along_se_m)} · ${pixels(m.boresight_px, m.boresight_se_px)}`,
          },
          {
            title: "标定来源架次",
            ellipsis: { showTitle: true },
            onCell: () => ({ className: "pb-mounts-cell-wrap" }),
            render: (_: unknown, m: PushbroomMount) => m.flights.join("、") || "—",
          },
          { title: "自动匹配", dataIndex: "matched", align: "center" as const },
          {
            title: "保存时间",
            render: (_: unknown, m: PushbroomMount) => formatJobDateTime(m.created_at).slice(0, 16),
          },
          {
            title: "操作",
            onCell: () => ({ style: { whiteSpace: "nowrap" } }),
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
    </div>
  );
}