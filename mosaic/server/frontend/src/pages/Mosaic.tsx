import { useEffect, useState } from "react";
import { Button, Card, Select, Space, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { api, type JobBandFile } from "../api";
import OrthoStage from "../components/OrthoStage";
import { useJobs } from "../jobsContext";
import { formatNo } from "../ids";

function sizeText(bytes: number) {
  if (!bytes) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** 查看拼图：整幅拖动、缩放，并按波段下载原文件。 */
export default function MosaicPage() {
  const { id: jobId } = useParams();
  const nav = useNavigate();
  const { jobs } = useJobs();
  const seq = jobs.find((item) => item.id === jobId)?.seq;
  const seqLabel = seq != null ? formatNo(seq) : "";
  const [bands, setBands] = useState<JobBandFile[]>([]);
  const [band, setBand] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (!jobId) return;
    let cancel = false;
    api
      .jobFiles(jobId)
      .then((data) => {
        if (cancel) return;
        setBands(data.bands);
        setBand((current) => current || data.bands[0]?.id || "");
      })
      .catch((error: Error) => message.error(error.message));
    return () => {
      cancel = true;
    };
  }, [jobId]);

  const current = bands.find((item) => item.id === band);

  async function download(kind: "file" | "all", name?: string) {
    if (!jobId) return;
    setBusy(kind);
    try {
      await api.downloadDeliverable(jobId, kind, name);
      message.success(kind === "file" ? "开始下载当前波段原文件" : "开始下载全部成果");
    } catch (error: any) {
      message.error(error.message || String(error));
    } finally {
      setBusy("");
    }
  }

  return (
    <Card
      className="mosaic-panel"
      title={seqLabel ? `拼图影像 ${seqLabel}` : "拼图影像"}
      extra={<Button onClick={() => nav("/jobs")}>返回记录</Button>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <span>波段</span>
          <Select
            style={{ minWidth: 140 }}
            value={band || undefined}
            placeholder="选择波段"
            options={bands.map((item) => ({ value: item.id, label: item.label }))}
            onChange={setBand}
          />
          <Button
            icon={<DownloadOutlined />}
            disabled={!current}
            loading={busy === "file"}
            onClick={() => current && download("file", current.name)}
          >
            下载当前波段原文件{current ? `（${sizeText(current.bytes)}）` : ""}
          </Button>
          <Button icon={<DownloadOutlined />} loading={busy === "all"} disabled={!bands.length} onClick={() => download("all")}>
            下载全部文件
          </Button>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          原文件是该波段的正射 GeoTIFF。全部文件包含各波段正射、DSM 和质量报告。
        </Typography.Paragraph>
        {jobId && band ? <OrthoStage jobId={jobId} band={band} /> : <Typography.Text type="secondary">还没有拼图成果。</Typography.Text>}
      </Space>
    </Card>
  );
}
