import { useMemo, useState } from "react";
import { Button, Card, Checkbox, Modal, Space, Table, Typography, message } from "antd";
import { EditOutlined } from "@ant-design/icons";
import { api } from "../api";
import { LIST_PAGE } from "../listPage";
import { useRbac } from "../rbac";
import { MENU_CATALOG, ROLE_LABEL, ROLE_ORDER, rolesForMenu, type MenuDef } from "../roles";

export default function PermissionsPage() {
  const { mapping, reload } = useRbac();
  const [editing, setEditing] = useState<MenuDef | null>(null);
  const [roles, setRoles] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const rows = useMemo(() => MENU_CATALOG, []);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>权限管理</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 项
          </Typography.Text>
        </Space>
      }
    >
      <Table
        rowKey="key"
        dataSource={rows}
        pagination={LIST_PAGE}
        columns={[
          { title: "权限名称", dataIndex: "label", width: 160 },
          { title: "所属模块", dataIndex: "group", width: 120 },
          { title: "说明", dataIndex: "hint" },
          {
            title: "操作",
            width: 100,
            render: (_: unknown, r: MenuDef) => (
              <Button
                type="link"
                icon={<EditOutlined />}
                style={{ padding: 0 }}
                onClick={() => {
                  setEditing(r);
                  setRoles(rolesForMenu(r, mapping));
                }}
              >
                编辑
              </Button>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? `编辑权限 · ${editing.label}` : "编辑权限"}
        open={Boolean(editing)}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onCancel={() => setEditing(null)}
        onOk={async () => {
          if (!editing) return;
          setSaving(true);
          try {
            await api.updatePermissionRoles(editing.key, roles);
            await reload();
            message.success("已保存");
            setEditing(null);
          } catch (e: any) {
            message.error(e.message || String(e));
          } finally {
            setSaving(false);
          }
        }}
      >
        <Typography.Paragraph type="secondary">{editing?.hint}</Typography.Paragraph>
        <Checkbox.Group
          style={{ display: "flex", flexDirection: "column", gap: 10 }}
          value={roles}
          onChange={(v) => setRoles(v.map(String))}
          options={ROLE_ORDER.map((id) => ({
            value: id,
            label: ROLE_LABEL[id],
            disabled: id === "admin",
          }))}
        />
      </Modal>
    </Card>
  );
}
