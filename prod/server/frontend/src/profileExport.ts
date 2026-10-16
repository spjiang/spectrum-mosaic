export const PROFILE_KIND = "ms_mosaic.param_profile";

export type ProfilePayload = {
  name: string;
  description: string;
  preset: string | null;
  values: Record<string, unknown>;
};

export function serializeProfile(p: {
  id?: number;
  name: string;
  description?: string;
  preset?: string | null;
  values?: Record<string, unknown>;
  version?: number;
}) {
  return {
    schema_version: 1,
    kind: PROFILE_KIND,
    name: p.name,
    description: p.description || "",
    preset: p.preset ?? null,
    values: p.values || {},
    source_id: p.id ?? null,
    source_version: p.version ?? null,
    exported_at: new Date().toISOString(),
  };
}

export function parseProfileFile(text: string): ProfilePayload {
  let data: any;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("不是合法的 JSON 文件");
  }
  if (Array.isArray(data)) throw new Error("请一次导入一份模版文件");
  if (!data || typeof data !== "object") throw new Error("文件内容为空");
  if (data.kind && data.kind !== PROFILE_KIND) throw new Error("不是参数模版文件");
  const values = data.values;
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    throw new Error("文件缺少 values 参数对象");
  }
  const name = String(data.name || "").trim() || "导入模版";
  return {
    name,
    description: String(data.description || ""),
    preset: data.preset ? String(data.preset) : null,
    values,
  };
}

export function uniqueProfileName(wanted: string, existing: string[]) {
  const set = new Set(existing);
  if (!set.has(wanted)) return wanted;
  const imported = `${wanted}（导入）`;
  if (!set.has(imported)) return imported;
  for (let i = 2; i < 100; i++) {
    const n = `${wanted}（导入${i}）`;
    if (!set.has(n)) return n;
  }
  return `${wanted}（导入${Date.now()}）`;
}

export function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function serializeJobParams(job: {
  id?: string;
  seq?: number | null;
  profile_id?: number | null;
  profile_version?: number | null;
  params_snapshot?: Record<string, unknown> | null;
}) {
  const values = job.params_snapshot || {};
  const no = job.seq != null ? `#${job.seq}` : "";
  return serializeProfile({
    name: no ? `任务${no} 运行参数` : "任务运行参数",
    description: `任务启动时写入数据库的参数快照，导入后可当模版使用。${job.id ? `来源任务 ${job.id}` : ""}`.trim(),
    preset: typeof values.preset === "string" ? values.preset : null,
    values,
    id: job.profile_id ?? undefined,
    version: job.profile_version ?? undefined,
  });
}

export function jobParamsFilename(job: { seq?: number | null; id?: string }) {
  if (job.seq != null) return `job-${job.seq}-params.json`;
  return `job-${String(job.id || "params").slice(0, 8)}-params.json`;
}

export function profileFilename(p: { id?: number; name: string }) {
  const safe = String(p.name || "profile")
    .replace(/[^\w\u4e00-\u9fff-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
  const id = p.id != null ? `${p.id}-` : "";
  return `param-profile-${id}${safe || "profile"}.json`;
}
