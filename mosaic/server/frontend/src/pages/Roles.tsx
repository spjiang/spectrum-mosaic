import { useEffect, useMemo, useState } from "react";
import { Button, Card, Drawer, Space, Table, Tag, Tree, Typography, message } from "antd";
import type { DataNode } from "antd/es/tree";
import { EditOutlined } from "@ant-design/icons";
import { api } from "../api";
import { LIST_PAGE } from "../listPage";
import { useRbac } from "../rbac";
import { MENU_CATALOG, MENU_GROUPS, ROLE_HINT, ROLE_LABEL, ROLE_ORDER, catalogFor, menuAvailable, menusForRole } from "../roles";

type Account = { roles: string[] };

export default function RolesPage() {
  const { mapping, features, reload: reloadRbac } = useRbac();
  const [users, setUsers] = useState<Account[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .users()
      .then((rows) => setUsers(Array.isArray(rows) ? rows : []))
      .catch(() => undefined);
  }, []);

  const rows = ROLE_ORDER.map((id) => ({
    id,
    name: ROLE_LABEL[id],
    hint: ROLE_HINT[id],
    users: users.filter((u) => (u.roles || []).includes(id)).length,
  }));

  const treeData: DataNode[] = useMemo(
    () =>
      MENU_GROUPS.map((g) => {
        const own = catalogFor(features).filter((m) => m.group === g.id);
        const leaf = (m: (typeof own)[number]) => ({ key: m.key, title: m.label });
        const children: DataNode[] = own.filter((m) => !m.sub).map(leaf);
        for (const sub of g.subgroups || []) {
          const items = own.filter((m) => m.sub === sub.id).map(leaf);
          if (items.length) children.push({ key: sub.siderKey, title: sub.id, children: items });
        }
        return { key: g.siderKey, title: g.id, children };
      }).filter((g) => g.children.length > 0),
    [features],
  );

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>角色管理</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 个
          </Typography.Text>
        </Space>
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "角色名称", dataIndex: "name", width: 160 },
          { title: "描述", dataIndex: "hint" },
          { title: "用户数", dataIndex: "users", width: 100 },
          {
            title: "操作",
            width: 100,
            render: (_: unknown, r: { id: string }) => (
              <Button
                type="link"
                icon={<EditOutlined />}
                style={{ padding: 0 }}
                onClick={() => {
                  setEditing(r.id);
                  setChecked(menusForRole(r.id, mapping).map((m) => m.key));
                }}
              >
                编辑
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        title={editing ? `编辑角色 · ${ROLE_LABEL[editing]}` : "编辑角色"}
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        width={480}
        destroyOnClose
        extra={editing === "admin" ? <Tag>系统管理员权限不可修改</Tag> : null}
        footer={
          <div style={{ textAlign: "right" }}>
            <Space>
              <Button onClick={() => setEditing(null)}>取消</Button>
              <Button
                type="primary"
                loading={saving}
                disabled={editing === "admin"}
                onClick={async () => {
                  if (!editing || editing === "admin") return;
                  setSaving(true);
                  try {
                    // 未开启的功能不在树里，已有授权原样带回，开启后不必重配
                    const hidden = menusForRole(editing, mapping).filter((m) => !menuAvailable(m, features));
                    await api.updateRoleMenus(editing, [...checked, ...hidden.map((m) => m.key)]);
                    await reloadRbac();
                    message.success("已保存");
                    setEditing(null);
                  } catch (e: any) {
                    message.error(e.message || String(e));
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                保存
              </Button>
            </Space>
          </div>
        }
      >
        {editing && (
          <>
            <Typography.Paragraph type="secondary">{ROLE_HINT[editing]}</Typography.Paragraph>
            <Typography.Text strong>功能权限</Typography.Text>
            <Tree
              checkable
              defaultExpandAll
              selectable={false}
              disabled={editing === "admin"}
              checkedKeys={checked}
              onCheck={(keys) => {
                const list = Array.isArray(keys) ? keys : keys.checked;
                setChecked(list.map(String).filter((k) => MENU_CATALOG.some((m) => m.key === k)));
              }}
              treeData={treeData}
              style={{ marginTop: 12 }}
            />
          </>
        )}
      </Drawer>
    </Card>
  );
}
