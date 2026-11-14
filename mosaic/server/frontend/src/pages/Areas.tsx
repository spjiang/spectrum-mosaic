import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Space, Switch, Table, Tooltip, Typography, message } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { api, type IncrementalProject } from "../api";

function indexText(index?: number | null) {
  if (index == null) return "还没有";
  return String(index).padStart(4, "0");
}

const FIELD_HELP = {
  name: "测区在列表里的名字，用来区分不同架次或区域。新建和以后编辑都可以改。",
  input_dir:
    "传完的航片会拷到这个目录，拼图只读这里。建立后不能改，避免和已经入库的文件对不上。收件区是这个路径后面加 .inbox。",
  output_root: "成果写在这个目录下面。每次重拼会新建一个时间子目录，测区仍然只绑定一条拼图任务。建立后不能改。",
  auto_run:
    "打开后，有新航片入库就会在这一条任务上开始拼图。关掉则只入库，等数据齐了再手动拼。任务正在跑时不会另开一条。",
  scan_interval_minutes:
    "后台每隔这么多分钟看一次收件区，页面关着也会扫。同一个文件连续两次大小没变，才算传完并拷进正式目录。可填 1 到 1440 分钟。",
};

function FieldLabel({ text, help }: { text: string; help: string }) {
  return (
    <span className="title-with-help">
      {text}
      <Tooltip
        trigger={["hover", "click"]}
        placement="bottomLeft"
        title={<span style={{ display: "inline-block", maxWidth: 320 }}>{help}</span>}
      >
        <button
          type="button"
          className="param-help-btn"
          aria-label={`${text}说明`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
        >
          <QuestionCircleOutlined />
        </button>
      </Tooltip>
    </span>
  );
}

export default function AreasPage() {
  const nav = useNavigate();
  const [rows, setRows] = useState<IncrementalProject[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<IncrementalProject | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  const load = useCallback(async (nextPage: number, nextSize: number) => {
    setLoading(true);
    try {
      const data = await api.incrementalProjects(nextPage, nextSize);
      setRows(data.items);
      setTotal(data.total);
      setPage(data.page);
      setPageSize(data.page_size);
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(page, pageSize).catch(() => undefined);
  }, [load, page, pageSize]);

  async function create(values: {
    name: string;
    input_dir: string;
    output_root: string;
    auto_run: boolean;
    scan_interval_minutes: number;
  }) {
    setLoading(true);
    try {
      await api.incrementalCreate(values);
      createForm.resetFields();
      setCreating(false);
      message.success("测区已建立");
      await load(1, pageSize);
      setPage(1);
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }

  async function saveEdit(values: { name: string; scan_interval_minutes: number; auto_run: boolean }) {
    if (!editing) return;
    setLoading(true);
    try {
      await api.incrementalPatch(editing.id, values);
      setEditing(null);
      message.success("测区已更新");
      await load(page, pageSize);
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }

  async function remove(row: IncrementalProject) {
    setLoading(true);
    try {
      await api.incrementalDelete(row.id);
      message.success("测区记录已删除，目录和成果文件仍保留");
      const nextPage = rows.length === 1 && page > 1 ? page - 1 : page;
      setPage(nextPage);
      await load(nextPage, pageSize);
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card
      title="测区管理"
      extra={
        <Button type="primary" onClick={() => setCreating(true)}>
          新建测区
        </Button>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (count) => `共 ${count} 个测区`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
        columns={[
          { title: "名称", dataIndex: "name", ellipsis: true },
          { title: "最新索引", dataIndex: "latest_shot_index", width: 110, render: (value) => indexText(value) },
          { title: "最新文件", dataIndex: "latest_filename", ellipsis: true, render: (value) => value || "—" },
          { title: "正式目录", dataIndex: "input_dir", ellipsis: true },
          {
            title: "当前成果",
            dataIndex: "current_output_dir",
            ellipsis: true,
            render: (value) => value || "还没有成功的拼图",
          },
          {
            title: "自动拼",
            dataIndex: "auto_run",
            width: 90,
            render: (value: boolean) => (value ? "开" : "关"),
          },
          { title: "扫描间隔", dataIndex: "scan_interval_minutes", width: 110, render: (value: number) => `${value} 分钟` },
          {
            title: "操作",
            width: 220,
            render: (_, row) => (
              <Space size={0}>
                <Button type="link" onClick={() => nav(`/incremental/${row.id}`)}>
                  进入
                </Button>
                <Button
                  type="link"
                  onClick={() => {
                    setEditing(row);
                    editForm.setFieldsValue({
                      name: row.name,
                      auto_run: row.auto_run,
                      scan_interval_minutes: row.scan_interval_minutes,
                    });
                  }}
                >
                  编辑
                </Button>
                <Popconfirm
                  title="删除这个测区？"
                  description="只删除测区记录。正式目录、收件区和已有成果文件都保留。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => remove(row)}
                >
                  <Button type="link" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title="新建测区"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => createForm.submit()}
        confirmLoading={loading}
        okText="建立测区"
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={create}
          initialValues={{ auto_run: false, scan_interval_minutes: 5 }}
        >
          <Form.Item name="name" label={<FieldLabel text="名称" help={FIELD_HELP.name} />} rules={[{ required: true, message: "请填写名称" }]}>
            <Input placeholder="例如 东区架次" />
          </Form.Item>
          <Form.Item
            name="input_dir"
            label={<FieldLabel text="正式目录" help={FIELD_HELP.input_dir} />}
            rules={[{ required: true, message: "请填写正式目录" }]}
          >
            <Input placeholder="/data/input/测区名" />
          </Form.Item>
          <Form.Item
            name="output_root"
            label={<FieldLabel text="输出根目录" help={FIELD_HELP.output_root} />}
            rules={[{ required: true, message: "请填写输出根目录" }]}
          >
            <Input placeholder="/data/output/runs/测区名" />
          </Form.Item>
          <Form.Item name="auto_run" label={<FieldLabel text="入库后自动拼" help={FIELD_HELP.auto_run} />} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="scan_interval_minutes"
            label={<FieldLabel text="扫描间隔（分钟）" help={FIELD_HELP.scan_interval_minutes} />}
            rules={[{ required: true, message: "请填写扫描间隔" }]}
          >
            <InputNumber min={1} max={1440} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="编辑测区"
        open={editing != null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        confirmLoading={loading}
        okText="保存"
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={saveEdit}>
          <Form.Item name="name" label={<FieldLabel text="名称" help={FIELD_HELP.name} />} rules={[{ required: true, message: "请填写名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item label={<FieldLabel text="正式目录" help={FIELD_HELP.input_dir} />}>
            <Typography.Text type="secondary">{editing?.input_dir}</Typography.Text>
          </Form.Item>
          <Form.Item label={<FieldLabel text="输出根目录" help={FIELD_HELP.output_root} />}>
            <Typography.Text type="secondary">{editing?.output_root}</Typography.Text>
          </Form.Item>
          <Form.Item name="auto_run" label={<FieldLabel text="入库后自动拼" help={FIELD_HELP.auto_run} />} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="scan_interval_minutes"
            label={<FieldLabel text="扫描间隔（分钟）" help={FIELD_HELP.scan_interval_minutes} />}
            rules={[{ required: true, message: "请填写扫描间隔" }]}
          >
            <InputNumber min={1} max={1440} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
