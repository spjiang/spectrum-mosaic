/** 任务 GPU 设置的展示文案：auto / off / 卡号。 */
export function gpuLabel(raw: unknown): string {
  if (raw == null || raw === "") return "自动";
  const text = String(raw).trim().toLowerCase();
  if (["off", "none", "cpu", "false", "no"].includes(text)) return "不用 GPU";
  if (/^\d+$/.test(text)) return `GPU ${Number(text)}`;
  return "自动";
}
