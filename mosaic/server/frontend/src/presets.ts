/** 内部枚举值保持英文，界面只展示中文。 */

export const PRESET_OPTIONS = [
  { value: "", label: "通用" },
  { value: "rgb_preview", label: "RGB 快速预览" },
] as const;

export const RUN_MODE_OPTIONS = [
  { value: "full", label: "全流程" },
  { value: "until_stage", label: "跑到指定阶段" },
  { value: "step", label: "逐步确认" },
] as const;

export const CAMERA_SOURCE_OPTIONS = [
  { value: "auto", label: "自动识别" },
  { value: "max_ms", label: "MAX 多光谱相机" },
  { value: "wayho_rgb", label: "Wayho VEN 系列 RGB 相机" },
] as const;

export function cameraSourceLabel(value: string | null | undefined): string {
  if (!value) return "自动识别";
  return CAMERA_SOURCE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export function presetLabel(value: string | null | undefined): string {
  if (!value) return "通用";
  return PRESET_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export function runModeLabel(value: string | null | undefined): string {
  if (!value) return "全流程";
  return RUN_MODE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}