import { Button, Card, Form, Input, Typography, message } from "antd";
import { useAuth } from "../auth";
import { PRODUCT } from "../product";
import BrandMark from "../components/BrandMark";

export default function LoginPage() {
  const { login } = useAuth();
  return (
    <div className="mosaic-login">
      <Card className="mosaic-login-card" bordered={false}>
        <div className="mosaic-login-brand">
          <BrandMark className="mosaic-brand-mark-lg" size={72} />
          <div>
            <Typography.Title className="mosaic-login-name" level={3}>
              {PRODUCT.name}
            </Typography.Title>
            <Typography.Text type="secondary" className="mosaic-login-tag">
              {PRODUCT.tagline}
            </Typography.Text>
          </div>
        </div>
        <Form
          layout="vertical"
          onFinish={async (v) => {
            try {
              await login(v.username, v.password);
            } catch (e: any) {
              message.error(e.message || "登录失败");
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
        <div className="mosaic-login-foot">{PRODUCT.copyright}</div>
      </Card>
    </div>
  );
}
