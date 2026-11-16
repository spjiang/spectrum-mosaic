import { useEffect, useState } from "react";
import { Button, Card, Space, Typography, message } from "antd";
import { DownloadOutlined, FileMarkdownOutlined, FilePdfOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, download } from "../api/client";

type ManualMeta = {
  title: string;
  markdown: string;
  has_pdf: boolean;
};

export default function ManualPage() {
  const [meta, setMeta] = useState<ManualMeta | null>(null);

  useEffect(() => {
    api<ManualMeta>("/api/manual")
      .then(setMeta)
      .catch((e) => message.error(String(e)));
  }, []);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>操作手册</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            提供给客户的部署与使用说明
          </Typography.Text>
        </Space>
      }
      extra={
        <Space wrap>
          <Button
            icon={<FileMarkdownOutlined />}
            onClick={() => download("/api/manual/download?format=md", "操作手册.md")}
          >
            下载 Markdown
          </Button>
          <Button
            type="primary"
            icon={<FilePdfOutlined />}
            disabled={!meta?.has_pdf}
            onClick={() => download("/api/manual/download?format=pdf", "操作手册.pdf")}
          >
            下载 PDF
          </Button>
        </Space>
      }
    >
      {!meta ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : (
        <>
          <div className="mosaic-manual-actions">
            <Typography.Text type="secondary">
              <DownloadOutlined /> 打交付包时会自动附带 `docs/操作手册.md` 与 `docs/操作手册.pdf`
            </Typography.Text>
          </div>
          <article className="mosaic-manual-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{meta.markdown}</ReactMarkdown>
          </article>
        </>
      )}
    </Card>
  );
}
