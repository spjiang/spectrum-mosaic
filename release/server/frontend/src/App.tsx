import { useMemo, useState } from "react";
import { Layout, Menu, Button, Dropdown, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  CloudServerOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  TeamOutlined,
  UserOutlined,
  AppstoreOutlined,
  GiftOutlined,
  ReadOutlined,
  SettingOutlined,
  AlertOutlined,
  ControlOutlined,
} from "@ant-design/icons";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import BrandMark from "./components/BrandMark";
import { getToken, setToken } from "./api/client";
import CustomersPage from "./pages/CustomersPage";
import DeliveriesPage from "./pages/DeliveriesPage";
import LicensesPage from "./pages/LicensesPage";
import LoginPage from "./pages/LoginPage";
import ManualPage from "./pages/ManualPage";
import NotesPage from "./pages/NotesPage";
import ReleasesPage from "./pages/ReleasesPage";
import SystemConfigPage from "./pages/SystemConfigPage";

const { Sider, Header, Content } = Layout;

const TITLES: Record<string, string> = {
  releases: "版本构建",
  customers: "客户档案",
  licenses: "许可签发",
  deliveries: "交付包",
  manual: "操作手册",
  notes: "注意事项",
  config: "全局配置",
};

function routeKey(pathname: string): string {
  if (pathname.startsWith("/settings/notes")) return "notes";
  if (pathname.startsWith("/settings/config")) return "config";
  return pathname.split("/")[1] || "releases";
}

function Shell({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const key = routeKey(loc.pathname);
  const pageTitle = TITLES[key] || "发版台";

  const items: MenuProps["items"] = useMemo(
    () => [
      {
        key: "grp-release",
        icon: <AppstoreOutlined />,
        label: "发版管理",
        children: [
          { key: "releases", icon: <CloudServerOutlined />, label: <Link to="/releases">版本构建</Link> },
          { key: "customers", icon: <TeamOutlined />, label: <Link to="/customers">客户档案</Link> },
          { key: "licenses", icon: <KeyOutlined />, label: <Link to="/licenses">许可签发</Link> },
          { key: "deliveries", icon: <GiftOutlined />, label: <Link to="/deliveries">交付包</Link> },
          { key: "manual", icon: <ReadOutlined />, label: <Link to="/manual">操作手册</Link> },
        ],
      },
      {
        key: "grp-settings",
        icon: <SettingOutlined />,
        label: "系统配置",
        children: [
          { key: "config", icon: <ControlOutlined />, label: <Link to="/settings/config">全局配置</Link> },
          { key: "notes", icon: <AlertOutlined />, label: <Link to="/settings/notes">注意事项</Link> },
        ],
      },
    ],
    []
  );

  const userMenu: MenuProps["items"] = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: () => {
        setToken(null);
        nav("/login");
      },
    },
  ];

  return (
    <Layout className="mosaic-shell" hasSider>
      <Sider
        className="mosaic-sider"
        width={232}
        collapsedWidth={72}
        collapsible
        collapsed={collapsed}
        trigger={null}
      >
        <div className={`mosaic-brand${collapsed ? " is-collapsed" : ""}`}>
          <BrandMark className="mosaic-brand-mark" size={collapsed ? 36 : 42} />
          {!collapsed && (
            <div>
              <div className="mosaic-brand-text">拼图发版台</div>
              <span className="mosaic-brand-sub">镜像 · 许可 · 交付</span>
            </div>
          )}
        </div>
        <Menu
          className="mosaic-sider-menu"
          mode="inline"
          selectedKeys={[key]}
          defaultOpenKeys={["grp-release", "grp-settings"]}
          items={items}
        />
        <div className="mosaic-sider-footer">
          {!collapsed && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              仅内部使用
            </Typography.Text>
          )}
        </div>
      </Sider>
      <Layout>
        <Header className="mosaic-header">
          <div className="mosaic-header-left">
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((v) => !v)}
            />
            <span className="mosaic-header-title">{pageTitle}</span>
            <span className="mosaic-header-product">高光谱拼图发版 1.0</span>
          </div>
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <Button type="text" icon={<UserOutlined />}>
              管理员
            </Button>
          </Dropdown>
        </Header>
        <Content className="mosaic-content">{children}</Content>
      </Layout>
    </Layout>
  );
}

function Private({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Navigate to="/releases" replace />} />
      <Route path="/releases" element={<Private><ReleasesPage /></Private>} />
      <Route path="/customers" element={<Private><CustomersPage /></Private>} />
      <Route path="/licenses" element={<Private><LicensesPage /></Private>} />
      <Route path="/deliveries" element={<Private><DeliveriesPage /></Private>} />
      <Route path="/manual" element={<Private><ManualPage /></Private>} />
      <Route path="/settings/config" element={<Private><SystemConfigPage /></Private>} />
      <Route path="/settings/notes" element={<Private><NotesPage /></Private>} />
    </Routes>
  );
}
