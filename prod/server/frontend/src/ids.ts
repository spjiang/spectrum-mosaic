export function formatNo(n: number | string | null | undefined, fallback = "—"): string {
  if (n == null || n === "") return fallback;
  const v = Number(n);
  if (!Number.isFinite(v)) return fallback;
  return `#${v}`;
}
