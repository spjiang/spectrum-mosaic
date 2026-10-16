export type LogLine = { ts: string; text: string };

const TS = /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(?:\s+\[[\w]+\]|\s+[A-Z]+)?\s+(.*)$/;

export function parseLogLine(raw: string): LogLine {
  const m = raw.match(TS);
  if (m) return { ts: m[1].replace("T", " ").slice(11, 19) || m[1], text: m[2] };
  return { ts: "", text: raw };
}

export function parseLogText(text: string): LogLine[] {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((line, i, all) => line.length > 0 || i < all.length - 1)
    .map(parseLogLine);
}
