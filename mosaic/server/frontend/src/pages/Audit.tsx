import { useEffect, useMemo, useState } from "react";
import { Card, Input, Space, Table, Typography, message } from "antd";
import { api } from "../api";
import { LIST_PAGE } from "../listPage";

type Row = {
  id: number;
  username: string | null;
  action: string;
  detail: Record<string, unknown>;
  created_at: string | null;
};

const ACTION_LABEL: Record<string, string> = {
  "job.create": "提交任务",
  "job.delete": "删除任务",
  "job.clear": "清空任务",
  "job.pause": "暂停任务",
  "job.resume": "继续运行",
  "job.cancel": "取消任务",
  "job.continue": "继续下一阶段",
  "job.retry": "重试任务",
  "job.recover": "清理并续跑",
  "job.kill_all": "杀死全部任务",
  "profile.create": "新建处理方案",
  "profile.update": "修改处理方案",
  "user.create": "新建用户",
  "user.update": "修改用户",
  "settings.update": "修改系统配置",
  "rbac.roles": "修改角色菜单",
  "rbac.permissions": "修改权限",
};

function formatTime(v?: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function detailText(action: string, detail: Record<string, unknown>) {
  const name = detail.name ?? detail.username;
  const seq = detail.seq;
  const id = detail.job_id ?? detail.id;
  if (action.startsWith("job.") && seq != null) return `任务 #${seq}`;
  if (action === "job.delete" && id) return String(id);
  if (action === "job.clear") return `删除 ${detail.deleted ?? 0} 条`;
  if (action === "job.kill_all") return `取消 ${detail.killed ?? 0} 条`;
  if (action.startsWith("profile.") && name) return String(name);
  if (action.startsWith("user.") && name) return String(name);
  if (action === "rbac.roles") return String(detail.role ?? "");
  if (action === "rbac.permissions") return String(detail.key ?? "");
  if (action === "settings.update") return "数据路径";
  return "";
}

export default function AuditPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");

  useEffect(() => {
    api
      .audits()
      .then((data) => setRows(Array.isArray(data) ? data : []))
      .catch((e: any) => message.error(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const k = keyword.trim().toLowerCase();
    if (!k) return rows;
    return rows.filter((r) => {
      const action = ACTION_LABEL[r.action] || r.action;
      const text = `${r.username || ""} ${action} ${detailText(r.action, r.detail || {})}`;
      return text.toLowerCase().includes(k);
    });
  }, [rows, keyword]);

  return (
    <Card
      className="mosaic-panel"
      title={
        <Space>
          <span>操作审计</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            共 {rows.length} 条
          </Typography.Text>
        </Space>
      }
      extra={
        <Input.Search
          allowClear
          placeholder="搜索用户或动作"
          style={{ width: 220 }}
          onSearch={setKeyword}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={filtered}
        pagination={{ ...LIST_PAGE, showTotal: (n) => `共 ${n} 条` }}
        columns={[
          { title: "时间", dataIndex: "created_at", width: 170, render: (v) => formatTime(v) },
          { title: "用户", dataIndex: "username", width: 140, render: (v) => v || "—" },
          {
            title: "动作",
            dataIndex: "action",
            width: 160,
            render: (v: string) => ACTION_LABEL[v] || v,
          },
          {
            title: "说明",
            render: (_, row) => detailText(row.action, row.detail || {}) || "—",
          },
        ]}
      />
    </Card>
  );
}
