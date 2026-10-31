import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  InputNumber,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { api, type IncrementalDetail } from "../api";
import JobStatusTag from "../components/JobStatusTag";

const STATUS_LABEL: Record<string, string> = {
  incoming: "上传中",
  ready: "已传完",
  ingested: "已入库",
};

const STATUS_COLOR: Record<string, string> = {
  incoming: "orange",
  ready: "blue",
  ingested: "green",
};

function hostPath(path: string) {
  if (path === "/data" || path.startsWith("/data/")) {
    return "/home/spectrum-mosaic/server/data" + path.slice("/data".length);
  }
  return path;
}

function shotTone(bands: IncrementalDetail["shots"][number]["bands"]) {
  if (bands.some((band) => band.status === "incoming")) return "incoming";
  if (bands.some((band) => band.status === "ready")) return "ready";
  return "ingested";
}

function indexText(index?: number | null) {
  if (index == null) return "还没有";
  return String(index).padStart(4, "0");
}

export default function IncrementalPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [current, setCurrent] = useState<IncrementalDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [shotPage, setShotPage] = useState(1);
  const [shotPageSize, setShotPageSize] = useState(10);

  const open = useCallback(async (projectId: string) => {
    const detail = await api.incrementalDetail(projectId);
    setCurrent(detail);
  }, []);

  useEffect(() => {
    if (!id) return;
    setShotPage(1);
    open(id).catch((err) => message.error(String(err.message || err)));
  }, [id, open]);

  useEffect(() => {
    if (!current?.id) return;
    const timer = window.setInterval(() => {
      api.incrementalDetail(current.id).then(setCurrent).catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [current?.id]);

  async function scan() {
    if (!current) return;
    setLoading(true);
    try {
      const detail = await api.incrementalScan(current.id);
      setCurrent(detail);
      if (detail.conflicts?.length) message.warning("这些文件大小不同，没有覆盖：" + detail.conflicts.join("、"));
      else if (detail.started_job_id) message.success("新航片已入库，已在当前任务上开始拼图");
      else message.success("已扫描收件区");
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }

  async function run() {
    if (!current) return;
    setLoading(true);
    try {
      const detail = await api.incrementalRun(current.id);
      setCurrent(detail);
      message.success(detail.job?.seq ? `已在任务 #${detail.job.seq} 上开始拼图` : "已在当前任务上开始拼图");
    } catch (err) {
      message.error(String((err as Error).message || err));
    } finally {
      setLoading(false);
    }
  }

  async function patch(body: { auto_run?: boolean; scan_interval_minutes?: number }) {
    if (!current) return;
    const detail = await api.incrementalPatch(current.id, body);
    setCurrent(detail);
  }

  const upload = current ? `rsync -av --progress ./新航片/ root@10.17.19.198:${hostPath(current.inbox_dir)}/` : "";

  if (!current) {
    return (
      <Card>
        <Empty description="正在读取测区" />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Button onClick={() => nav("/incremental")}>返回测区管理</Button>
      <Card title={current.name}>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {current.busy && (
            <Alert type="warning" showIcon message="这条任务正在拼。新传完的文件会先入库，等它结束后在同一条任务上再拼。" />
          )}
          {current.waiting_for_job && (
            <Alert type="warning" showIcon message="正式目录里已经有比这次拼图更新的航片，当前任务结束后会带上它们再拼一次。" />
          )}
          {current.blocked_by_other && !current.busy && (
            <Alert type="warning" showIcon message="别的拼图任务还在跑。新文件可以入库，这条任务要等它结束后再开始。" />
          )}
          {current.has_new_files && !current.auto_run && (
            <Alert type="info" showIcon message="有新航片已经入库。自动拼是关的，需要时再点「在当前任务上拼图」。" />
          )}
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="最新索引">
              <Typography.Text strong>{indexText(current.latest_shot_index)}</Typography.Text>
              <Typography.Text type="secondary"> {current.latest_filename || "还没有文件入库"}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="正式目录">{current.input_dir}</Descriptions.Item>
            <Descriptions.Item label="收件区">{hostPath(current.inbox_dir)}</Descriptions.Item>
            <Descriptions.Item label="上传命令">
              <Typography.Paragraph copyable={{ text: upload }} style={{ marginBottom: 0 }}>
                <Typography.Text code>{upload}</Typography.Text>
              </Typography.Paragraph>
            </Descriptions.Item>
            <Descriptions.Item label="当前任务">
              {current.job?.seq ? `#${current.job.seq}` : "还没有任务"} {current.job ? <JobStatusTag status={current.job.status} /> : null}
              {current.current_output_dir ? `　成果 ${current.current_output_dir}` : ""}
            </Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <span>入库后自动拼</span>
            <Switch checked={current.auto_run} onChange={(checked) => patch({ auto_run: checked })} />
            <span>每</span>
            <InputNumber
              min={1}
              max={1440}
              value={current.scan_interval_minutes}
              onChange={(value) => value && patch({ scan_interval_minutes: value })}
            />
            <span>分钟扫描一次收件区</span>
            <Button onClick={scan} loading={loading}>
              立即扫描入库
            </Button>
            <Button type="primary" onClick={run} loading={loading} disabled={current.busy || current.blocked_by_other}>
              在当前任务上拼图
            </Button>
          </Space>
          <Typography.Text type="secondary">
            上传中 {current.incoming} · 已传完 {current.ready} · 已入库 {current.ingested}。同一曝光号的新片子、或后到的新波长，都传到收件区。大小连续两次扫描不变，就拷进正式目录。
          </Typography.Text>
          <Table
            rowKey="shot_index"
            size="middle"
            dataSource={current.shots}
            pagination={{
              current: shotPage,
              pageSize: shotPageSize,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (total) => `共 ${total} 个曝光`,
              onChange: (nextPage, size) => {
                setShotPage(nextPage);
                setShotPageSize(size);
              },
            }}
            columns={[
              {
                title: "曝光号",
                dataIndex: "shot_index",
                width: 110,
                render: (value: number) => (
                  <Space size={6}>
                    <span>{indexText(value)}</span>
                    {value === current.latest_shot_index ? <Tag color="blue">最新</Tag> : null}
                  </Space>
                ),
              },
              {
                title: "状态",
                width: 100,
                render: (_, shot) => {
                  const tone = shotTone(shot.bands);
                  return <Tag color={STATUS_COLOR[tone]}>{STATUS_LABEL[tone]}</Tag>;
                },
              },
              {
                title: "波段",
                dataIndex: "bands",
                render: (bands: IncrementalDetail["shots"][number]["bands"]) => (
                  <Space size={[4, 4]} wrap>
                    {bands.map((band) => (
                      <Tag key={band.filename} color={STATUS_COLOR[band.status] || "default"}>
                        {band.band} {STATUS_LABEL[band.status] || band.status}
                      </Tag>
                    ))}
                  </Space>
                ),
              },
            ]}
          />
        </Space>
      </Card>
    </Space>
  );
}
