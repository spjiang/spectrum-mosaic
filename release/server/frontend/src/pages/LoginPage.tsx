import { Button, Card, Form, Input, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import { api, setToken } from "../api/client";

export default function LoginPage() {
  const nav = useNavigate();

  return (
    <div className="mosaic-login">
      <Card className="mosaic-login-card" bordered={false}>
        <div className="mosaic-login-brand">
          <BrandMark size={72} />
          <div>
            <Typography.Title className="mosaic-login-name" level={3}>
              拼图发版台
            </Typography.Title>
            <Typography.Text type="secondary" className="mosaic-login-tag">
              内部签发 · 客户镜像交付
            </Typography.Text>
          </div>
        </div>
        <Form
          layout="vertical"
          initialValues={{ username: "admin", password: "admin123" }}
          onFinish={async (v) => {
            try {
              const data = await api<{ access_token: string }>("/api/auth/login", {
                method: "POST",
                body: JSON.stringify(v),
              });
              setToken(data.access_token);
              nav("/releases");
            } catch (e: unknown) {
              message.error(e instanceof Error ? e.message : "登录失败");
            }
          }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input autoFocus size="large" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password size="large" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large">
            登录
          </Button>
        </Form>
        <div className="mosaic-login-foot">仅部署在我方环境，不进入客户包</div>
      </Card>
    </div>
  );
}
