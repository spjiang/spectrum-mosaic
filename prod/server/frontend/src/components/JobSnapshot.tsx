import { useEffect, useState } from "react";
import { Collapse, Descriptions, Typography } from "antd";
import { api } from "../api";
import { gpuLabel } from "../gpuLabel";
import type { ParamDef } from "../paramRules";
import { presetLabel, runModeLabel } from "../presets";
import { STAGE_META, STAGES, stageLabel, type StageId } from "../stages";

type Def = ParamDef & { sort_order?: number };

/** 方案里已取消。快照里仍可能留着参数表旧默认值，不在任务详情里展示。 */
const HIDDEN = new Set(["workers_at"]);

const EXTRA: Record<string, { stage: StageId; label: string; sort: number }> = {
  products_dir: { stage: "S0_io", label: "成果目录", sort: 65 },
  native_gsd_m: { stage: "S5_ortho", label: "原始分辨率（米）", sort: 8 },
  ortho_gsd_m: { stage: "S5_ortho", label: "拼图分辨率（米）", sort: 9 },
  report_pdf_path: { stage: "S6_report", label: "质量报告路径", sort: 30 },
};

let defsPromise: Promise<Def[]> | null = null;

function loadDefs(): Promise<Def[]> {
  if (!defsPromise) {
    defsPromise = api.paramDefs().catch(() => {
      defsPromise = null;
      return [] as Def[];
    });
  }
  return defsPromise;
}

function fmt(key: string, value: unknown): string {
  if (value == null || value === "") return "—";
  if (key === "run_mode") return runModeLabel(String(value));
  if (key === "preset") return presetLabel(value ? String(value) : null);
  if (key === "start_stage" || key === "stop_after_stage") return stageLabel(String(value));
  if (key === "gpu") return gpuLabel(value);
  if (key === "color_correction") {
    return value === "on" || value === "off_for_ms" ? "是（只校正 RGB）" : "否";
  }
  if (key === "memory_gb" && Number(value) === 0) return "自动";
  if (key === "cpus" && Number(value) === 0) return "自动";
  if (key === "gpu_memory_gb" && Number(value) === 0) return "自动（空闲显存 85%）";
  if (typeof value === "boolean") return value ? "开" : "关";
  if (Array.isArray(value)) return value.length ? value.map(String).join("、") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function wide(key: string, value: unknown, valueType?: string): boolean {
  if (valueType === "path" || valueType === "string[]") return true;
  if (key.endsWith("_dir") || key.endsWith("_path") || key === "bands" || key === "at_bands") return true;
  return fmt(key, value).length > 42;
}

export function JobSnapshotBody({
  snapshot,
  profileId,
  profileVersion,
}: {
  snapshot?: Record<string, unknown> | null;
  profileId?: number | null;
  profileVersion?: number | null;
}) {
  const snap = snapshot || {};
  const [defs, setDefs] = useState<Def[]>([]);

  useEffect(() => {
    let live = true;
    loadDefs().then((rows) => {
      if (live) setDefs(rows);
    });
    return () => {
      live = false;
    };
  }, []);

  const byKey = new Map(defs.map((d) => [d.key, d]));
  const placed = new Set<string>();
  const groups = STAGES.map((stage) => {
    const rows = Object.keys(snap).filter((key) => !HIDDEN.has(key))
      .map((key) => {
        const def = byKey.get(key);
        const extra = EXTRA[key];
        const stageId = (def?.stage_id || extra?.stage) as StageId | undefined;
        if (stageId !== stage) return null;
        placed.add(key);
        return {
          key,
          label: def?.label?.trim() || extra?.label || key,
          sort: def?.sort_order ?? extra?.sort ?? 1000,
          valueType: def?.value_type,
        };
      })
      .filter((row): row is NonNullable<typeof row> => row != null)
      .sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label, "zh"));
    return { stage, rows };
  }).filter((g) => g.rows.length > 0);

  const rest = Object.keys(snap)
    .filter((key) => !placed.has(key) && !HIDDEN.has(key))
    .sort();

  const body = (
            <div>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 12 }}>
                点击执行时写入的参数，按处理流程节点排列
                {profileId != null ? `（方案 #${profileId} v${profileVersion ?? "-"}）` : ""}
                。后来改方案不会改这里。空三进程数按本次任务的 CPU 核数计算，不单列。
              </Typography.Paragraph>
              {groups.map(({ stage, rows }) => (
                <div key={stage} className="job-snapshot-stage">
                  <div className="job-snapshot-stage-title">
                    {STAGES.indexOf(stage)} {STAGE_META[stage].label}
                    <span>{stage}</span>
                  </div>
                  <Descriptions size="small" column={2} bordered>
                    {rows.map((row) => (
                      <Descriptions.Item
                        key={row.key}
                        label={row.label}
                        span={wide(row.key, snap[row.key], row.valueType) ? 2 : 1}
                      >
                        {fmt(row.key, snap[row.key])}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                </div>
              ))}
              {rest.length > 0 && (
                <div className="job-snapshot-stage">
                  <div className="job-snapshot-stage-title">
                    其他
                  </div>
                  <Descriptions size="small" column={2} bordered>
                    {rest.map((key) => (
                      <Descriptions.Item key={key} label={key} span={wide(key, snap[key]) ? 2 : 1}>
                        {fmt(key, snap[key])}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                </div>
              )}
            </div>
  );

  return body;
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
  return (
    <Collapse
      size="small"
      defaultActiveKey={defaultOpen ? ["snap"] : []}
      items={[
        {
          key: "snap",
          label: "当时保存的配置",
          children: (
            <JobSnapshotBody snapshot={snapshot} profileId={profileId} profileVersion={profileVersion} />
          ),
        },
      ]}
    />
  );
}
