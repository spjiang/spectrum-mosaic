import { useEffect, useRef, useState } from "react";
import { Button, Descriptions, Table, Tabs, Tag, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { api, type PushbroomFile, type PushbroomJob } from "../../../api";
import { formatJobDateTime } from "../../../jobTime";
import ProductHelp from "../ProductHelp";
import {
  PB_ACTIVE,
  PB_STAGES,
  flightSize,
  formatBandList,
  formatBytes,
  formatDuration,
  jobElapsedSeconds,
  jobMountText,
} from "../labels";

export default function JobTabs({ job }: { job: PushbroomJob }) {
  const [files, setFiles] = useState<PushbroomFile[]>([]);
  const [logs, setLogs] = useState("");
  const logRef = useRef<HTMLPreElement>(null);
  const active = PB_ACTIVE.includes(job.status);

  useEffect(() => {
    let cancelled = false;
    api
      .pushbroomFiles(job.id)
      .then((r) => !cancelled && setFiles(r.items))
      .catch(() => !cancelled && setFiles([]));
    return () => {
      cancelled = true;
    };
  }, [job.id, job.status]);

  useEffect(() => {
    let cancelled = false;
    api
      .pushbroomLogs(job.id)
      .then((text) => !cancelled && setLogs(text))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [job.id, job.updated_at]);

  useEffect(() => {
    const el = logRef.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [logs, active]);

  async function download(f: PushbroomFile) {
    try {
      await api.pushbroomDownload(job.id, f.path);
    } catch (err) {
      message.error((err as Error).message || String(err));
    }
  }

  return (
    <Tabs
      items={[
        {
          key: "files",
          label: (
            <span className="title-with-help">
              成果文件
              <ProductHelp />
            </span>
          ),
          children: (
            <Table
              rowKey="path"
              size="small"
              dataSource={files}
              pagination={false}
              locale={{ emptyText: job.status === "succeeded" ? "成果目录为空" : "完成后在这里下载" }}
              columns={[
                { title: "文件", dataIndex: "name", ellipsis: true },
                { title: "类别", dataIndex: "kind", width: 90, render: (k: string) => (k === "report" ? "报告" : "成果") },
                { title: "大小", dataIndex: "size", width: 100, render: (n: number) => formatBytes(n) },
                {
                  title: "",
                  width: 90,
                  render: (_: unknown, f: PushbroomFile) => (
                    <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => download(f)}>
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          ),
        },
        {
          key: "params",
          label: "处理参数",
          children: (
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="架次目录" span={2}>
                <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                  {job.input_dir}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="成果目录" span={2}>
                <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                  {job.output_dir}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="安装配置" span={2}>
                {jobMountText(job.params)}
              </Descriptions.Item>
              <Descriptions.Item label="分辨率">
                {job.params.gsd_m ? `${(job.params.gsd_m * 100).toFixed(1)} cm` : "自动"}
              </Descriptions.Item>
              <Descriptions.Item label="输出波段">{formatBandList(job.params.bands)}</Descriptions.Item>
              <Descriptions.Item label="影像尺寸">{flightSize(job.flight)}</Descriptions.Item>
              <Descriptions.Item label="原始数据">{formatBytes(job.flight.spe_bytes)}</Descriptions.Item>
              <Descriptions.Item label="提交人">{job.created_by || "—"}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{formatJobDateTime(job.created_at)}</Descriptions.Item>
              <Descriptions.Item label="运行次数">第 {job.run_attempt} 次</Descriptions.Item>
              <Descriptions.Item label="耗时">{formatDuration(jobElapsedSeconds(job))}</Descriptions.Item>
            </Descriptions>
          ),
        },
        {
          key: "timings",
          label: "各步耗时",
          children: (
            <div className="pb-timings">
              {PB_STAGES.map((s) => {
                const sec = job.stage_timings[s.id];
                const current = job.current_stage === s.id && active;
                return (
                  <Tag key={s.id} color={sec != null ? "success" : current ? "processing" : "default"}>
                    {s.label}
                    {sec != null ? ` ${formatDuration(sec)}` : current ? " 进行中" : ""}
                  </Tag>
                );
              })}
            </div>
          ),
        },
        {
          key: "logs",
          label: "运行日志",
          children: (
            <pre ref={logRef} className="pb-logs">
              {logs || "(暂无日志)"}
            </pre>
          ),
        },
      ]}
    />
  );
}