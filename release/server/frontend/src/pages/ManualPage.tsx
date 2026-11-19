import { useEffect, useState } from "react";
import { Button, Card, Space, Tabs, Typography, message } from "antd";
import { DownloadOutlined, FileMarkdownOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, download } from "../api/client";

type ManualDoc = {
  key: string;
  title: string;
  filename: string;
  markdown: string;
};

type ManualMeta = {
  title: string;
  docs: ManualDoc[];
};

export default function ManualPage() {
  const [meta, setMeta] = useState<ManualMeta | null>(null);
  const [active, setActive] = useState("deploy");

  useEffect(() => {
    api<ManualMeta>("/api/manual")
      .then((data) => {
        setMeta(data);
        if (data.docs[0]) setActive(data.docs[0].key);
      })
      .catch((e) => message.error(String(e)));
  }, []);

  const current = meta?.docs.find((doc) => doc.key === active) ?? meta?.docs[0];

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
        current ? (
          <Button
            type="primary"
            icon={<FileMarkdownOutlined />}
            onClick={() => download(`/api/manual/download?doc=${current.key}`, current.filename)}
          >
            下载{current.title}
          </Button>
        ) : null
      }
    >
      {!meta ? (
        <Typography.Text type="secondary">加载中…</Typography.Text>
      ) : (
        <>
          <div className="mosaic-manual-actions">
            <Typography.Text type="secondary">
              <DownloadOutlined /> 打交付包时附带这两份 Markdown
            </Typography.Text>
          </div>
          <Tabs
            activeKey={current?.key}
            onChange={setActive}
            items={meta.docs.map((doc) => ({
              key: doc.key,
              label: doc.title,
              children: (
                <article className="mosaic-manual-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.markdown}</ReactMarkdown>
                </article>
              ),
            }))}
          />
        </>
      )}
    </Card>
  );
}
