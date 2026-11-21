import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AimOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  EditOutlined,
  FileSearchOutlined,
  HeartOutlined,
  PlayCircleOutlined,
  ProfileOutlined,
  SafetyOutlined,
  ScanOutlined,
  SettingOutlined,
  TeamOutlined,
  ToolOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useAuth } from "../auth";
import BrandMark from "../components/BrandMark";
import { PRODUCT } from "../product";
import { useRbac } from "../rbac";
import { MENU_GROUPS, menusForRoles, type MenuGroup, type MenuKey } from "../roles";

const ENTRY_ICON: Record<MenuKey, ReactNode> = {
  execute: <PlayCircleOutlined />,
  incremental: <CloudUploadOutlined />,
  pushbroom_workbench: <ScanOutlined />,
  pushbroom_mounts: <AimOutlined />,
  profiles: <ProfileOutlined />,
  jobs: <UnorderedListOutlined />,
  seam: <EditOutlined />,
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
  框幅拼图: <AppstoreOutlined />,
  推扫拼图: <ScanOutlined />,
  辅助工具: <ToolOutlined />,
  系统管理: <SettingOutlined />,
  组织管理: <TeamOutlined />,
};

export default function HomePage() {
  const { roles } = useAuth();
  const { mapping, features } = useRbac();
  const menus = menusForRoles(roles, mapping, features);
  const groups = MENU_GROUPS.map((group) => ({
    ...group,
    items: menus.filter((item) => item.group === group.id),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="home-page">
      <section className="home-hero">
        <BrandMark size={56} />
        <div>
          <h1>{PRODUCT.name}</h1>
          <p>框幅正射、推扫高光谱和系统管理从这里进入。打开本站时停在首页，需要处理时再进入对应功能。</p>
        </div>
      </section>
      {groups.map((group) => (
        <section key={group.id} className="home-group">
          <h2>
            {GROUP_ICON[group.id]}
            <span>{group.id}</span>
          </h2>
          <div className="home-grid">
            {group.items.map((item) => (
              <Link key={item.key} to={item.path} className="home-entry">
                <span className="home-entry-icon">{ENTRY_ICON[item.key]}</span>
                <span className="home-entry-copy">
                  <strong>{item.label}</strong>
                  <span>{item.hint}</span>
                </span>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
