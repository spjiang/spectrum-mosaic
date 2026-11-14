import { useEffect, useRef, useState } from "react";
import { parseLogText } from "../logLines";

export default function LogConsole({
  text,
  empty = "（暂无日志）",
  follow = true,
}: {
  text: string;
  empty?: string;
  follow?: boolean;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  const [pinned, setPinned] = useState(true);
  const lines = parseLogText(text);

  useEffect(() => {
    const el = box.current;
    if (!el || !follow || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  }, [text, follow, lines.length]);

  return (
    <div className="log-console">
      <div className="log-console-bar">
        <span>{pinned ? "已跟随最新" : "已停在上方"}</span>
        {!pinned && (
          <button
            type="button"
            className="log-console-jump"
            onClick={() => {
              stick.current = true;
              setPinned(true);
              const el = box.current;
              if (el) el.scrollTop = el.scrollHeight;
            }}
          >
            跳到最后
          </button>
        )}
      </div>
      <div
        className="mosaic-log log-console-body"
        ref={box}
        onScroll={() => {
          const el = box.current;
          if (!el) return;
          const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
          stick.current = atEnd;
          setPinned(atEnd);
        }}
      >
        {lines.length === 0 || (lines.length === 1 && !lines[0].text) ? (
          <div className="log-console-empty">{empty}</div>
        ) : (
          lines.map((line, i) => (
            <div className="log-console-row" key={`${i}-${line.ts}-${line.text.slice(0, 24)}`}>
              <span className="log-console-ts">{line.ts || "·"}</span>
              <span className="log-console-msg">{line.text || " "}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
