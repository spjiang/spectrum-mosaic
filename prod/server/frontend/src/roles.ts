export const ROLE_LABEL: Record<string, string> = {
  admin: "系统管理员",
  configurator: "配置管理员",
  executor: "操作员",
  viewer: "只读用户",
};

export const ROLE_ORDER = ["admin", "configurator", "executor", "viewer"] as const;

export const ROLE_HINT: Record<string, string> = {
  admin: "拥有系统全部权限，可管理用户、配置、计算资源与处理任务。",
  executor: "可创建、暂停和删除处理任务，并查看处理方案与处理记录。",
  configurator: "可维护处理方案与系统配置，不可创建或管控处理任务。",
  viewer: "可查看处理方案与处理记录，不可修改配置或提交任务。",
};

export type MenuKey =
  | "execute"
  | "profiles"
  | "jobs"
  | "worker"
  | "health"
  | "settings"
  | "inspect"
  | "users"
  | "roles"
  | "permissions"
  | "audit"
  | "cli";

export type MenuGroup = "作业管理" | "辅助工具" | "系统管理" | "组织管理";

export type MenuDef = {
  key: MenuKey;
  label: string;
  path: string;
  group: MenuGroup;
  roles: readonly string[];
  hint: string;
};

export const MENU_GROUPS: { id: MenuGroup; siderKey: string }[] = [
  { id: "作业管理", siderKey: "proc" },
  { id: "辅助工具", siderKey: "tools" },
  { id: "系统管理", siderKey: "sys" },
  { id: "组织管理", siderKey: "rbac" },
];

export const MENU_CATALOG: MenuDef[] = [
  { key: "execute", label: "新建处理", path: "/execute", group: "作业管理", roles: ["admin", "executor"], hint: "创建正射与 DSM 处理任务" },
  { key: "profiles", label: "处理方案", path: "/profiles", group: "作业管理", roles: ["admin", "configurator", "executor", "viewer"], hint: "查看与维护处理参数方案" },
  { key: "jobs", label: "处理记录", path: "/jobs", group: "作业管理", roles: ["admin", "configurator", "executor", "viewer"], hint: "查看任务进度、结果与历史" },
  { key: "worker", label: "计算节点", path: "/worker", group: "系统管理", roles: ["admin"], hint: "监控计算进程与任务队列" },
  { key: "health", label: "系统监控", path: "/health", group: "系统管理", roles: ["admin"], hint: "查看数据库、消息队列与计算服务状态" },
  { key: "settings", label: "系统配置", path: "/settings", group: "系统管理", roles: ["admin", "configurator"], hint: "配置数据路径等运行参数" },
  { key: "inspect", label: "影像查看", path: "/inspect", group: "辅助工具", roles: ["admin", "configurator", "executor", "viewer"], hint: "上传 TIF / JPG，查看元数据与像元" },
  { key: "cli", label: "使用文档", path: "/cli", group: "辅助工具", roles: ["admin", "configurator", "executor", "viewer"], hint: "流程、算法与参数说明" },
  { key: "users", label: "用户管理", path: "/users", group: "组织管理", roles: ["admin"], hint: "维护账号、状态与角色分配" },
  { key: "roles", label: "角色管理", path: "/roles", group: "组织管理", roles: ["admin"], hint: "维护系统角色与授权用户" },
  { key: "permissions", label: "权限管理", path: "/permissions", group: "组织管理", roles: ["admin"], hint: "维护功能权限及授权角色" },
  { key: "audit", label: "操作审计", path: "/audit", group: "组织管理", roles: ["admin"], hint: "查看谁改了方案、谁提交或删除了任务" },
];

export function menuVisible(item: MenuDef, roles: string[]): boolean {
  if (roles.includes("admin")) return true;
  return item.roles.some((r) => roles.includes(r));
}

export function menusForRoles(roles: string[], mapping?: Record<string, string[]>): MenuDef[] {
  if (roles.includes("admin")) return [...MENU_CATALOG];
  const keys = new Set<string>();
  for (const role of roles) {
    const granted = mapping?.[role] ?? MENU_CATALOG.filter((item) => item.roles.includes(role)).map((item) => item.key);
    for (const key of granted) keys.add(key);
  }
  return MENU_CATALOG.filter((item) => keys.has(item.key));
}

export function menusForRole(role: string, mapping?: Record<string, string[]>): MenuDef[] {
  return menusForRoles([role], mapping);
}

export function rolesForMenu(item: MenuDef, mapping?: Record<string, string[]>): string[] {
  return ROLE_ORDER.filter((role) => {
    if (role === "admin") return true;
    const granted = mapping?.[role] ?? item.roles;
    return granted.includes(item.key);
  });
}

export function primaryRole(roles: string[]): string | null {
  if (roles.includes("admin")) return "admin";
  if (roles.includes("configurator")) return "configurator";
  if (roles.includes("executor")) return "executor";
  if (roles.includes("viewer")) return "viewer";
  return roles[0] || null;
}

export function roleLabel(roles: string[]): string {
  const id = primaryRole(roles);
  return (id && ROLE_LABEL[id]) || "无角色";
}

export function canExecute(roles: string[]): boolean {
  return roles.includes("admin") || roles.includes("executor");
}

export function canConfigure(roles: string[]): boolean {
  return roles.includes("admin") || roles.includes("configurator");
}

export function canViewOps(roles: string[]): boolean {
  return roles.includes("admin");
}

export function canEditSettings(roles: string[]): boolean {
  return canConfigure(roles);
}

export function homePath(roles: string[]): string {
  if (canExecute(roles)) return "/execute";
  if (canConfigure(roles)) return "/profiles";
  return "/jobs";
}

export function rolesFromToken(token: string | null): string[] {
  if (!token) return [];
  try {
    const part = token.split(".")[1];
    if (!part) return [];
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(json);
    return Array.isArray(payload.roles) ? payload.roles.map(String) : [];
  } catch {
    return [];
  }
}
