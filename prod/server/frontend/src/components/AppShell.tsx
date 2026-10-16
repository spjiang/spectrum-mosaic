import { useMemo, useState, type ReactNode } from "react";
import { Layout, Menu, Progress, Button, Tooltip, Dropdown, Tag, Modal, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  PlayCircleOutlined,
  ProfileOutlined,
  UnorderedListOutlined,
  BookOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  UserOutlined,
  DownOutlined,
  CloudServerOutlined,
  HeartOutlined,
  SettingOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  SafetyOutlined,
  TeamOutlined,
  AppstoreOutlined,
  ToolOutlined,
  AuditOutlined,
} from "@ant-design/icons";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { JobsProvider, useJobs } from "../jobsContext";
import ExecutePage from "../pages/Execute";
import ProfilesListPage from "../pages/ProfilesList";
import ProfileEditPage from "../pages/ProfileEdit";
import JobsPage from "../pages/Jobs";
import CliGuidePage from "../pages/CliGuide";
import HealthPage from "../pages/Health";
import WorkerPage from "../pages/Worker";
import SettingsPage from "../pages/Settings";
import InspectPage from "../pages/Inspect";
import UsersPage from "../pages/Users";
import RolesPage from "../pages/Roles";
import PermissionsPage from "../pages/Permissions";
import AuditPage from "../pages/Audit";
import { PRODUCT, PRODUCT_TITLE } from "../product";
import {
  canConfigure,
  homePath,
  menusForRoles,
  roleLabel,
  MENU_GROUPS,
  type MenuGroup,
  type MenuKey,
} from "../roles";
import { useRbac } from "../rbac";
import BrandMark from "./BrandMark";
import { formatNo } from "../ids";

const { Sider, Header, Content } = Layout;

const TITLE: Record<string, string> = {
  execute: "新建处理",
  profiles: "处理方案",
  jobs: "处理记录",
  worker: "计算节点",
  health: "系统监控",
  settings: "系统配置",
  inspect: "影像查看",
  users: "用户管理",
  roles: "角色管理",
  permissions: "权限管理",
  cli: "使用文档",
  audit: "操作审计",
};

const MENU_ICON: Record<MenuKey, ReactNode> = {
  execute: <PlayCircleOutlined />,
  profiles: <ProfileOutlined />,
  jobs: <UnorderedListOutlined />,
  worker: <CloudServerOutlined />,
  health: <HeartOutlined />,
  settings: <SettingOutlined />,
  inspect: <FileSearchOutlined />,
  users: <UserOutlined />,
  roles: <TeamOutlined />,
  permissions: <SafetyOutlined />,
  cli: <BookOutlined />,
  audit: <AuditOutlined />,
};

const GROUP_ICON: Record<MenuGroup, ReactNode> = {
  作业管理: <AppstoreOutlined />,
  辅助工具: <ToolOutlined />,
  系统管理: <SettingOutlined />,
  组织管理: <TeamOutlined />,
};

export default function AppShell() {
  const { token, logout, username, roles } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return (
    <JobsProvider>
      <AppShellBody logout={logout} username={username} roles={roles} />
    </JobsProvider>
  );
}

function Guard({ allow, children }: { allow: boolean; children: React.ReactNode }) {
  const { roles } = useAuth();
  if (!allow) return <Navigate to={homePath(roles)} replace />;
  return <>{children}</>;
}

function AppShellBody({
  logout,
  username,
  roles,
}: {
  logout: () => void;
  username: string | null;
  roles: string[];
}) {
  const loc = useLocation();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [about, setAbout] = useState(false);
  const [openKeys, setOpenKeys] = useState<string[]>(MENU_GROUPS.map((g) => g.siderKey));
  const { activeJob } = useJobs();
  const { mapping } = useRbac();
  const conf = canConfigure(roles);
  const visibleMenus = useMemo(() => menusForRoles(roles, mapping), [roles, mapping]);
  const canMenu = (id: string) => visibleMenus.some((m) => m.key === id);

  const key = loc.pathname.split("/")[1] || "execute";
  const pageTitle =
    key === "profiles" && loc.pathname.includes("/edit")
      ? "编辑处理方案"
      : key === "profiles" && loc.pathname.includes("/new")
        ? "新建处理方案"
        : TITLE[key] || PRODUCT.name;

  const menuItems = useMemo(() => {
    const visible = visibleMenus;
    const toItem = (m: (typeof visible)[number]) => ({
      key: m.key,
      icon: MENU_ICON[m.key],
      label: <Link to={m.path}>{m.label}</Link>,
    });
    return MENU_GROUPS.flatMap((group) => {
      const children = visible.filter((m) => m.group === group.id).map(toItem);
      if (!children.length) return [];
      return [
        {
          key: group.siderKey,
          icon: GROUP_ICON[group.id],
          label: group.id,
          children,
        },
      ];
    });
  }, [visibleMenus]);

  const userMenuItems: MenuProps["items"] = useMemo(
    () => [
      { key: "about", icon: <InfoCircleOutlined />, label: "关于" },
      { type: "divider" },
      { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
    ],
    []
  );

  return (
    <Layout className="mosaic-shell">
      <Sider
        className="mosaic-sider"
        collapsible
        collapsed={collapsed}
        trigger={null}
        width={248}
        collapsedWidth={80}
      >
        <div className={`mosaic-brand${collapsed ? " is-collapsed" : ""}`}>
          <BrandMark className="mosaic-brand-mark" size={collapsed ? 40 : 44} />
          {!collapsed && (
            <div className="mosaic-brand-copy">
              <div className="mosaic-brand-text">{PRODUCT.name}</div>
              <span className="mosaic-brand-sub">{PRODUCT.tagline}</span>
            </div>
          )}
        </div>
        <Menu
          className="mosaic-sider-menu"
          mode="inline"
          selectedKeys={[key]}
          openKeys={collapsed ? [] : openKeys}
          onOpenChange={setOpenKeys}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Header className="mosaic-header">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((c) => !c)}
            />
            <div className="mosaic-header-title">
              <span className="mosaic-header-title-main">{pageTitle}</span>
              <span className="mosaic-header-title-sub">{PRODUCT_TITLE}</span>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            {activeJob && (
              <Tooltip title={`${activeJob.status} · ${activeJob.message || ""}`}>
                <div
                  style={{ minWidth: 140, cursor: "pointer" }}
                  onClick={() => nav(`/execute?job=${activeJob.id}`)}
                >
                  <div style={{ color: "var(--mosaic-muted)", fontSize: 11, marginBottom: 2 }}>
                    任务 {formatNo(activeJob.seq, String(activeJob.id).slice(0, 8))}
                  </div>
                  <Progress
                    percent={Math.round(activeJob.global_percent || 0)}
                    size="small"
                    status={
                      activeJob.status === "failed"
                        ? "exception"
                        : activeJob.status === "paused"
                          ? "normal"
                          : "active"
                    }
                    showInfo
                  />
                </div>
              </Tooltip>
            )}
            <Dropdown
              menu={{
                items: userMenuItems,
                onClick: ({ key: k }) => {
                  if (k === "logout") logout();
                  if (k === "about") setAbout(true);
                },
              }}
              placement="bottomRight"
              trigger={["click"]}
            >
              <Button type="text" className="mosaic-user-trigger">
                <UserOutlined />
                <span>{username || "用户"}</span>
                <Tag className="mosaic-role-tag">{roleLabel(roles)}</Tag>
                <DownOutlined style={{ fontSize: 10 }} />
              </Button>
            </Dropdown>
          </div>
        </Header>
        <Content className="mosaic-content">
          <Routes>
            <Route path="/execute" element={<ExecutePage />} />
            <Route path="/profiles" element={<ProfilesListPage />} />
            <Route
              path="/profiles/new"
              element={
                <Guard allow={conf}>
                  <ProfileEditPage />
                </Guard>
              }
            />
            <Route path="/profiles/:id/edit" element={<ProfileEditPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route
              path="/worker"
              element={
                <Guard allow={canMenu("worker")}>
                  <WorkerPage />
                </Guard>
              }
            />
            <Route
              path="/health"
              element={
                <Guard allow={canMenu("health")}>
                  <HealthPage />
                </Guard>
              }
            />
            <Route
              path="/inspect"
              element={
                <Guard allow={canMenu("inspect")}>
                  <InspectPage />
                </Guard>
              }
            />
            <Route
              path="/settings"
              element={
                <Guard allow={canMenu("settings")}>
                  <SettingsPage />
                </Guard>
              }
            />
            <Route
              path="/users"
              element={
                <Guard allow={canMenu("users")}>
                  <UsersPage />
                </Guard>
              }
            />
            <Route
              path="/roles"
              element={
                <Guard allow={canMenu("roles")}>
                  <RolesPage />
                </Guard>
              }
            />
            <Route
              path="/permissions"
              element={
                <Guard allow={canMenu("permissions")}>
                  <PermissionsPage />
                </Guard>
              }
            />
            <Route
              path="/audit"
              element={
                <Guard allow={canMenu("audit")}>
                  <AuditPage />
                </Guard>
              }
            />
            <Route path="/access" element={<Navigate to="/users" replace />} />
            <Route
              path="/cli"
              element={
                <Guard allow={canMenu("cli")}>
                  <CliGuidePage />
                </Guard>
              }
            />
            <Route path="*" element={<Navigate to={homePath(roles)} replace />} />
          </Routes>
        </Content>
      </Layout>
      <Modal
        open={about}
        onCancel={() => setAbout(false)}
        footer={null}
        title="关于"
        centered
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 12 }}>
          <BrandMark size={48} />
          <div>
            <Typography.Title level={4} style={{ margin: 0, letterSpacing: "0.12em" }}>
              {PRODUCT.name}
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              {PRODUCT.tagline} · 版本 {PRODUCT.version}
            </Typography.Paragraph>
          </div>
        </div>
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          当前登录：{username || "—"}（{roleLabel(roles)}）
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
          {PRODUCT.copyright}
        </Typography.Paragraph>
      </Modal>
    </Layout>
  );
}
