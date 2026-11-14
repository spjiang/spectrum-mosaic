import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button, Card, Col, Modal, Row, Space, Spin, Typography, message } from "antd";
import {
  CheckCircleFilled,
  CloseCircleFilled,
  ReloadOutlined,
  DatabaseOutlined,
  CloudServerOutlined,
  PlaySquareOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { api } from "../api";

type StatusPayload = {
  status: string;
  postgres: string;
  rabbitmq: string;
  worker: string;
};

function StatusCard({
  title,
  icon,
  value,
}: {
  title: string;
  icon: ReactNode;
  value: string | undefined;
}) {
  const ok = value === "ok";
  return (
    <Card className="mosaic-panel" size="small">
      <Space align="start" size={12}>
        <span style={{ fontSize: 22, color: "var(--mosaic-primary)" }}>{icon}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <Typography.Text strong>{title}</Typography.Text>
            {ok ? (
              <CheckCircleFilled style={{ color: "var(--mosaic-ok, #16a34a)" }} />
            ) : (
              <CloseCircleFilled style={{ color: "var(--mosaic-bad, #dc2626)" }} />
            )}
            <Typography.Text type={ok ? "success" : "danger"}>{ok ? "正常" : "异常"}</Typography.Text>
          </div>
          <Typography.Paragraph
            type="secondary"
            style={{ marginBottom: 0, fontSize: 12, wordBreak: "break-all" }}
            ellipsis={{ rows: 4, expandable: true, symbol: "展开" }}
          >
            {value || "未检查"}
          </Typography.Paragraph>
        </div>
      </Space>
    </Card>
  );
}

export default function HealthPage() {
  const [data, setData] = useState<StatusPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData((await api.systemStatus()) as StatusPayload);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload().catch((e) => {
      setLoading(false);
      message.error(String(e));
    });
  }, [reload]);

  return (
    <Card
      className="mosaic-panel"
      title="健康检查"
      extra={
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={loading}
          disabled={loading}
          onClick={() => reload().catch((e) => message.error(String(e)))}
        >
          重新检查
        </Button>
      }
    >
      <Modal
        open={loading}
        footer={null}
        closable={false}
        maskClosable={false}
        keyboard={false}
        centered
        width={280}
      >
        <div style={{ textAlign: "center", padding: "8px 0 4px" }}>
          <Spin size="large" />
          <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}>检查中</Typography.Paragraph>
        </div>
      </Modal>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        每次进入本页都会实时探测 PostgreSQL、RabbitMQ、Worker。Worker 已监听 mosaic.jobs
        即为正常。要看容器里的进程和当前计算任务，打开{" "}
        <Link to="/worker">Worker 监控</Link>。
      </Typography.Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <StatusCard title="PostgreSQL" icon={<DatabaseOutlined />} value={data?.postgres} />
        </Col>
        <Col xs={24} md={8}>
          <StatusCard title="RabbitMQ" icon={<CloudServerOutlined />} value={data?.rabbitmq} />
        </Col>
        <Col xs={24} md={8}>
          <StatusCard title="Worker" icon={<PlaySquareOutlined />} value={data?.worker} />
        </Col>
      </Row>
    </Card>
  );
}
