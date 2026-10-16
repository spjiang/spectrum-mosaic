import { useEffect, useState } from "react";
import { Card, Typography, message } from "antd";
import { api } from "../api";
import MarkdownDoc from "../components/MarkdownDoc";

const TABS = [
  { key: "cli", tab: "命令行手册" },
  { key: "params", tab: "参数说明" },
  { key: "quality", tab: "质量报告手册" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function CliGuidePage() {
  const [tab, setTab] = useState<TabKey>("params");
  const [cliMd, setCliMd] = useState("");
  const [paramMd, setParamMd] = useState("");
  const [qualityMd, setQualityMd] = useState("");
  const [cliLoading, setCliLoading] = useState(true);
  const [paramLoading, setParamLoading] = useState(true);
  const [qualityLoading, setQualityLoading] = useState(true);

  useEffect(() => {
    api
      .cliGuide()
      .then(setCliMd)
      .catch((e) => message.error(String(e)))
      .finally(() => setCliLoading(false));
    api
      .paramGuide()
      .then(setParamMd)
      .catch((e) => message.error(String(e)))
      .finally(() => setParamLoading(false));
    api
      .qualityGuide()
      .then(setQualityMd)
      .catch((e) => message.error(String(e)))
      .finally(() => setQualityLoading(false));
  }, []);

  return (
    <Card
      className="mosaic-panel mosaic-cli-guide"
      tabList={[...TABS]}
      activeTabKey={tab}
      onTabChange={(key) => setTab(key as TabKey)}
    >
      {tab === "cli" ? (
        <MarkdownDoc
          md={cliMd}
          loading={cliLoading}
          intro={
            <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
              ① 部署安装 → ② 命令行参数 → ③ 本机测区示例。相对路径均相对主程序安装目录。
            </Typography.Paragraph>
          }
        />
      ) : tab === "quality" ? (
        <MarkdownDoc
          md={qualityMd}
          loading={qualityLoading}
          intro={
            <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
              按 <strong>质量报告.pdf</strong> 的栏名逐项说明。左侧目录与报告里的字段一一对应。
            </Typography.Paragraph>
          }
        />
      ) : (
        <MarkdownDoc
          md={paramMd}
          loading={paramLoading}
          intro={
            <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
              按进度条 0～6 节点说明：每个阶段开头有<strong>白话</strong>，再写算法与参数。改大、改小、关闭的效果在各表最后两列。
            </Typography.Paragraph>
          }
        />
      )}
    </Card>
  );
}
