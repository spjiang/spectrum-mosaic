import { useEffect, useState, type ReactNode } from "react";
import { Button, Card, Col, Row, Segmented, Statistic, Table, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { api, type QualityView } from "../api";
import { useJobs } from "../jobsContext";
import { formatNo } from "../ids";

const FIGURE_LABEL: Record<string, string> = {
  ortho: "正射",
  dsm: "DSM",
  camera_pos: "相机位置",
  at_view: "空三视图",
  overlap: "重叠度",
};

function num(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/** 质量报告可视化。照片定位差值来自空三，不是野外检测点。 */
export default function QualityPage() {
  const { id: jobId } = useParams();
  const nav = useNavigate();
  const { jobs } = useJobs();
  const seq = jobs.find((item) => item.id === jobId)?.seq;
  const seqLabel = seq != null ? formatNo(seq) : "";
  const [view, setView] = useState<QualityView | null>(null);
  const [figures, setFigures] = useState<Record<string, string>>({});
  const [pane, setPane] = useState<"summary" | "pdf">("summary");
  const [pdfUrl, setPdfUrl] = useState("");

  useEffect(() => {
    if (!jobId) return;
    let cancel = false;
    api
      .quality(jobId)
      .then((next) => {
        if (!cancel) setView(next);
      })
      .catch((error: Error) => message.error(error.message));
    return () => {
      cancel = true;
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobId || !view?.figures?.length) return;
    let cancel = false;
    const urls: string[] = [];
    Promise.all(
      view.figures.map(async (name) => {
        const url = await api.qualityFigure(jobId, name);
        urls.push(url);
        return [name, url] as const;
      }),
    )
      .then((pairs) => {
        if (cancel) {
          pairs.forEach(([, url]) => url && URL.revokeObjectURL(url));
          return;
        }
        setFigures(Object.fromEntries(pairs.filter(([, url]) => url)));
      })
      .catch((error: Error) => message.error(error.message));
    return () => {
      cancel = true;
      urls.forEach((url) => url && URL.revokeObjectURL(url));
    };
  }, [jobId, view]);

  useEffect(() => {
    if (!jobId || pane !== "pdf" || !view?.has_pdf || pdfUrl) return;
    let cancel = false;
    api
      .jobReportUrl(jobId)
      .then((url) => {
        if (cancel || !url) {
          if (url) URL.revokeObjectURL(url);
          return;
        }
        setPdfUrl(url);
      })
      .catch((error: Error) => {
        if (!cancel) message.error(error.message);
      });
    return () => {
      cancel = true;
    };
  }, [jobId, pane, view?.has_pdf, pdfUrl]);

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  async function downloadPdf() {
    if (!jobId) return;
    try {
      await api.openJobReport(jobId);
    } catch (error: any) {
      message.error(error.message || String(error));
    }
  }

  const at = view?.at;
  const dsm = view?.dsm;
  const ortho = view?.ortho;

  return (
    <Card
      className="mosaic-panel"
      title={seqLabel ? `质量报告 ${seqLabel}` : "质量报告"}
      extra={
        <SpaceGap>
          <Button icon={<DownloadOutlined />} disabled={!view?.has_pdf} onClick={downloadPdf}>
            下载 PDF
          </Button>
          <Button onClick={() => nav("/jobs")}>返回记录</Button>
        </SpaceGap>
      }
    >
      {!view ? (
        <Typography.Paragraph>正在读取质量报告…</Typography.Paragraph>
      ) : (
        <div className="quality-page">
          <Segmented
            value={pane}
            options={[
              { label: "汇总", value: "summary" },
              { label: "PDF", value: "pdf", disabled: !view.has_pdf },
            ]}
            onChange={(value) => setPane(value as "summary" | "pdf")}
          />
          {pane === "pdf" ? (
            pdfUrl ? (
              <iframe className="quality-pdf" title="质量报告 PDF" src={pdfUrl} />
            ) : (
              <Typography.Paragraph>{view.has_pdf ? "正在打开 PDF…" : view.message || "质量报告尚未生成。"}</Typography.Paragraph>
            )
          ) : !view.ready ? (
            <Typography.Paragraph>{view.message || "质量报告尚未生成。"}</Typography.Paragraph>
          ) : (
            <>
          <Typography.Paragraph type="secondary">
            {view.project_name ? `${view.project_name}　` : ""}
            {view.crs_label}
            {view.created_at ? `　${view.created_at}` : ""}
          </Typography.Paragraph>
          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}><Statistic title="扫描曝光" value={view.n_scanned ?? "—"} /></Col>
            <Col xs={12} md={6}><Statistic title="采用曝光" value={view.n_shots ?? "—"} /></Col>
            <Col xs={12} md={6}><Statistic title="已注册影像" value={view.n_registered ?? "—"} suffix={view.n_images != null ? `/ ${view.n_images}` : ""} /></Col>
            <Col xs={12} md={6}><Statistic title="测区面积" value={num(view.area_km2, 3)} suffix="km²" /></Col>
            <Col xs={12} md={6}><Statistic title="平均航高" value={num(view.mean_agl_m, 1)} suffix="m" /></Col>
            <Col xs={12} md={6}><Statistic title="正射地面分辨率" value={num(ortho?.gsd, 4)} suffix="m" /></Col>
            <Col xs={12} md={6}><Statistic title="DSM 地面分辨率" value={num(dsm?.gsd, 4)} suffix="m" /></Col>
            <Col xs={12} md={6}><Statistic title="重投影均方根" value={num(at?.rms_reprojection_px, 3)} suffix="像素" /></Col>
            <Col xs={12} md={6}><Statistic title="连接点" value={at?.n_points ?? "—"} /></Col>
            <Col xs={12} md={6}><Statistic title="照片定位三维均方根" value={num(at?.gps_rmse_m, 3)} suffix="m" /></Col>
            <Col xs={12} md={6}><Statistic title="东 / 北 / 高" value={`${num(at?.gps_rmse_x_m, 2)} / ${num(at?.gps_rmse_y_m, 2)} / ${num(at?.gps_rmse_z_m, 2)}`} suffix="m" /></Col>
            <Col xs={12} md={6}><Statistic title="DSM 高程中值" value={num(dsm?.z_median, 2)} suffix="m" /></Col>
          </Row>
          <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
            照片定位差值是空三后的相机位置相对曝光时定位的偏差，不是野外检测点。高程为椭球高。正射方式：{ortho?.mode || "—"}，融合：{ortho?.blend || "—"}。DSM 有效覆盖 {num((dsm?.fill_ratio || 0) * 100, 1)}%，高程范围 {num(dsm?.z_min, 1)}～{num(dsm?.z_max, 1)} m。
          </Typography.Paragraph>
          <Table
            size="small"
            pagination={false}
            rowKey="band"
            dataSource={view.cameras || []}
            columns={[
              { title: "波段", dataIndex: "band" },
              { title: "影像", dataIndex: "n_images" },
              { title: "已注册", dataIndex: "n_registered" },
              { title: "重投影 RMS（像素）", dataIndex: "rms_px", render: (value: number | null) => num(value, 3) },
              { title: "焦距（像素）", dataIndex: "f", render: (value: number | null) => num(value, 1) },
            ]}
          />
          {view.histogram?.length ? (
            <>
              <Typography.Title level={5}>照片定位差值分布（%）</Typography.Title>
              <Table
                size="small"
                pagination={false}
                rowKey="label"
                dataSource={view.histogram}
                columns={[
                  { title: "区间 m", dataIndex: "label" },
                  { title: "三维", dataIndex: "error", render: (value: number | null) => num(value, 1) },
                  { title: "东", dataIndex: "dx", render: (value: number | null) => num(value, 1) },
                  { title: "北", dataIndex: "dy", render: (value: number | null) => num(value, 1) },
                  { title: "高", dataIndex: "dz", render: (value: number | null) => num(value, 1) },
                ]}
              />
            </>
          ) : null}
          <div className="quality-figures">
            {(view.figures || []).map((name) =>
              figures[name] ? (
                <figure key={name}>
                  <img src={figures[name]} alt={FIGURE_LABEL[name] || name} />
                  <figcaption>{FIGURE_LABEL[name] || name}</figcaption>
                </figure>
              ) : null,
            )}
          </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

function SpaceGap({ children }: { children: ReactNode }) {
  return <span style={{ display: "inline-flex", gap: 8 }}>{children}</span>;
}
