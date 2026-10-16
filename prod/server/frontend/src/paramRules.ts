export type ParamDef = {
  key: string;
  stage_id: string;
  value_type: string;
  default_value: any;
  description: string;
  label?: string | null;
  advanced?: boolean;
  required?: boolean;
  required_when?: Record<string, any> | null;
};

export function isEmpty(value: any): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && !value.trim()) return true;
  if (Array.isArray(value) && value.length === 0) return true;
  return false;
}

export function isRequired(d: ParamDef, values: Record<string, any>): boolean {
  if (d.required) return true;
  const when = d.required_when;
  if (!when || typeof when !== "object") return false;
  return Object.entries(when).every(([key, expect]) => {
    const got = values[key];
    return Array.isArray(expect) ? expect.includes(got) : got === expect;
  });
}

export function paramRemark(d: ParamDef): string {
  if (d.label && d.label.trim()) return d.label.trim();
  const text = (d.description || "").trim();
  if (!text) return "";
  return text.split(/[。；\n]/)[0] || text;
}

export function formatDefault(d: ParamDef): string {
  const v = d.default_value;
  if (v === null || v === undefined) return "空（自动 / 不启用）";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

export function validateValues(defs: ParamDef[], values: Record<string, any>): { key: string; message: string }[] {
  const errors: { key: string; message: string }[] = [];
  for (const d of defs) {
    const raw = values[d.key];
    const need = isRequired(d, values);
    if (need && isEmpty(raw)) {
      errors.push({
        key: d.key,
        message: d.required_when ? `${d.key} 在当前运行模式下为必填` : `${d.key} 为必填`,
      });
      continue;
    }
    if (isEmpty(raw)) continue;
    if ((d.value_type === "int" || d.value_type === "float") && typeof raw === "number" && Number.isNaN(raw)) {
      errors.push({ key: d.key, message: `${d.key} 须为有效数字` });
      continue;
    }
    if (
      d.value_type === "path" &&
      typeof raw === "string" &&
      ["input_dir", "output_dir", "cache_dir", "log_dir", "process_dir", "reuse_dsm"].includes(d.key) &&
      !raw.trim().startsWith("/data")
    ) {
      errors.push({ key: d.key, message: `${d.key} 须位于容器 /data 下` });
    }
  }
  return errors;
}
