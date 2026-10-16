import { useEffect, useId, useRef } from "react";
import mermaid from "mermaid";

let ready = false;

function ensureMermaid() {
  if (ready) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "neutral",
    securityLevel: "loose",
    fontFamily: '"Source Han Sans SC", "Noto Sans SC", "PingFang SC", "IBM Plex Sans", sans-serif',
    themeVariables: {
      fontFamily: '"Source Han Sans SC", "Noto Sans SC", "PingFang SC", "IBM Plex Sans", sans-serif',
      fontSize: "16px",
    },
    flowchart: {
      htmlLabels: true,
      useMaxWidth: false,
      padding: 16,
      nodeSpacing: 36,
      rankSpacing: 40,
      wrappingWidth: 280,
    },
  });
  ready = true;
}

function growAttr(el: Element, name: string, delta: number) {
  el.setAttribute(name, String(Number(el.getAttribute(name) || 0) + delta));
}

/** mermaid 按拉丁字宽估节点，中文会被 foreignObject 裁掉。按实际文字撑开。 */
function fitCjkLabels(root: HTMLElement) {
  root.querySelectorAll("g.node").forEach((g) => {
    const fo = g.querySelector("foreignObject");
    if (!fo) return;
    const inner = fo.querySelector(".nodeLabel, span, div, p") as HTMLElement | null;
    if (!inner) return;
    const needW = Math.ceil(Math.max(inner.scrollWidth, inner.offsetWidth) + 28);
    const needH = Math.ceil(Math.max(inner.scrollHeight, inner.offsetHeight) + 16);
    const curW = Number(fo.getAttribute("width")) || 0;
    const curH = Number(fo.getAttribute("height")) || 0;
    const dw = Math.max(0, needW - curW);
    const dh = Math.max(0, needH - curH);
    if (dw === 0 && dh === 0) return;
    growAttr(fo, "width", dw);
    growAttr(fo, "height", dh);
    growAttr(fo, "x", -dw / 2);
    growAttr(fo, "y", -dh / 2);
    const shape = g.querySelector("rect");
    if (shape) {
      growAttr(shape, "width", dw);
      growAttr(shape, "height", dh);
      growAttr(shape, "x", -dw / 2);
      growAttr(shape, "y", -dh / 2);
    }
  });
}

export default function MermaidBlock({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const reactId = useId().replace(/:/g, "");

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      ensureMermaid();
      const id = `mmd-${reactId}-${Math.random().toString(36).slice(2, 8)}`;
      try {
        const { svg } = await mermaid.render(id, chart.trim());
        if (cancelled || !ref.current) return;
        ref.current.innerHTML = svg;
        requestAnimationFrame(() => {
          if (cancelled || !ref.current) return;
          fitCjkLabels(ref.current);
        });
      } catch (err) {
        if (!cancelled && ref.current) ref.current.textContent = String(err);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [chart, reactId]);

  return <div className="mosaic-mermaid" ref={ref} />;
}
