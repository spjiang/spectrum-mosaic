import { useEffect, useState } from "react";
import { Button, Card, Space, Typography, message } from "antd";
import { FileMarkdownOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, download } from "../api/client";

type ManualMeta = {
  title: string;
  markdown: string;
  has_pdf: boolean;
};

export default function InternalManualPage() {
  const [meta, setMeta] = useState<ManualMeta | null>(null);

  useEffect(() => {
    api<ManualMeta>("/api/manual/internal")
      .then(setMeta)
      .catch((e) => message.error(String(e)));
  }, []);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>内部手册</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            发版、密钥、许可签发与交付包。不进入客户包
          </Typography.Text>
        </Space>
      }
      extra={
        <Button
          icon={<FileMarkdownOutlined />}
          onClick={() => download("/api/manual/internal/download", "内部操作手册.md")}
        >
          下载 Markdown
        </Button>
      }
    >
      {!meta ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : (
        <article className="mosaic-manual-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{meta.markdown}</ReactMarkdown>
        </article>
      )}
    </Card>
  );
}
