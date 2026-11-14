import type { HTMLAttributes, MouseEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

type HeaderProps = HTMLAttributes<HTMLTableCellElement> & {
  width?: number;
  minWidth?: number;
  onResize?: (width: number) => void;
  children?: ReactNode;
};

export function ResizableHeader({ width, minWidth = 72, onResize, children, className, style, ...rest }: HeaderProps) {
  return (
    <th {...rest} className={`mosaic-th-resizable ${className || ""}`} style={{ ...style, width, minWidth }}>
      {children}
      {onResize ? (
        <span
          className="mosaic-col-resizer"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e: MouseEvent) => {
            e.preventDefault();
            e.stopPropagation();
            const startX = e.clientX;
            const startW = width || minWidth;
            const move = (ev: globalThis.MouseEvent) => onResize(Math.max(minWidth, startW + ev.clientX - startX));
            const up = () => {
              document.body.classList.remove("is-col-resizing");
              document.removeEventListener("mousemove", move);
              document.removeEventListener("mouseup", up);
            };
            document.body.classList.add("is-col-resizing");
            document.addEventListener("mousemove", move);
            document.addEventListener("mouseup", up);
          }}
        />
      ) : null}
    </th>
  );
}

export function useColumnWidths(storageKey: string, defaults: Record<string, number>) {
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) return { ...defaults, ...JSON.parse(raw) };
    } catch {
      /* 忽略坏缓存 */
    }
    return { ...defaults };
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(widths));
    } catch {
      /* 隐私模式 */
    }
  }, [storageKey, widths]);

  const setWidth = useCallback((key: string, width: number) => {
    setWidths((prev) => ({ ...prev, [key]: width }));
  }, []);

  const total = useMemo(() => Object.values(widths).reduce((a, b) => a + b, 0), [widths]);
  return { widths, setWidth, total };
}
