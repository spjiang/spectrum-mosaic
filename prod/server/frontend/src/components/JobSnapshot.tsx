import { Collapse, Descriptions, Typography } from "antd";

const HIGHLIGHT = [
  "input_dir",
  "output_dir",
  "bands",
  "run_mode",
  "start_stage",
  "stop_after_stage",
  "dsm_gsd",
  "cache_dir",
  "workers_at",
  "memory_gb",
  "cpus",
  "benchmark_dir",
  "match_reference_color",
  "reuse_dsm",
];

function fmt(v: unknown) {
  if (v == null || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function JobSnapshot({
  snapshot,
  profileId,
  profileVersion,
  defaultOpen = false,
}: {
  snapshot?: Record<string, unknown> | null;
  profileId?: number | null;
  profileVersion?: number | null;
  defaultOpen?: boolean;
}) {
  const snap = snapshot || {};
  const keys = Object.keys(snap).sort();
  const rest = keys.filter((k) => !HIGHLIGHT.includes(k));

  return (
    <Collapse
      size="small"
      defaultActiveKey={defaultOpen ? ["snap"] : []}
      items={[
        {
          key: "snap",
          label: "当时保存的配置",
          children: (
            <div>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 12 }}>
                点击执行时写入数据库的参数快照
                {profileId != null ? `（模版 #${profileId} v${profileVersion ?? "-"}）` : ""}
                ，不会随后来改模版而变。
              </Typography.Paragraph>
              <Descriptions size="small" column={1} bordered>
                {HIGHLIGHT.filter((k) => k in snap).map((k) => (
                  <Descriptions.Item key={k} label={k}>
                    {fmt(snap[k])}
                  </Descriptions.Item>
                ))}
                {rest.map((k) => (
                  <Descriptions.Item key={k} label={k}>
                    {fmt(snap[k])}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </div>
          ),
        },
      ]}
    />
  );
}
