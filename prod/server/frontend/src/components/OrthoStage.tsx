import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Space, Typography } from "antd";
import { api, type AccuracyView } from "../api";

/** 整幅正射一次载入，拖动和缩放只改画面，不再向服务器要窗口。 */
export default function OrthoStage({ jobId, band }: { jobId: string; band: string }) {
  const [image, setImage] = useState<AccuracyView | null>(null);
  const [placed, setPlaced] = useState(false);
  const [note, setNote] = useState("");
  const overviewUrl = useRef("");
  const sharpUrl = useRef("");
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const cameraRef = useRef({ x: 0, y: 0, scale: 1 });
  const layerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<AccuracyView | null>(null);
  const fittedRef = useRef("");

  function applyCamera() {
    const layer = layerRef.current;
    const cam = cameraRef.current;
    if (!layer) return;
    layer.style.transform = `translate3d(${cam.x}px, ${cam.y}px, 0) scale(${cam.scale})`;
  }

  function fitImage(next: AccuracyView) {
    const canvas = canvasRef.current;
    if (!canvas || next.width <= 0 || next.height <= 0) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return;
    const scale = Math.min(rect.width / next.width, rect.height / next.height);
    cameraRef.current = {
      scale,
      x: (rect.width - next.width * scale) / 2,
      y: (rect.height - next.height * scale) / 2,
    };
    applyCamera();
  }

  function lookAt(col: number, row: number) {
    const current = imageRef.current;
    const canvas = canvasRef.current;
    if (!current || !canvas || current.width <= 0 || current.fullWidth <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    const px = (col / current.fullWidth) * current.width;
    const py = (row / current.fullHeight) * current.height;
    cameraRef.current = {
      scale: cam.scale,
      x: rect.width / 2 - px * cam.scale,
      y: rect.height / 2 - py * cam.scale,
    };
    applyCamera();
  }

  function nudge(dx: number, dy: number) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    cameraRef.current = { ...cam, x: cam.x + dx * rect.width, y: cam.y + dy * rect.height };
    applyCamera();
  }

  function zoomBy(factor: number) {
    const canvas = canvasRef.current;
    const current = imageRef.current;
    if (!canvas || !current || current.width <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const px = (cx - cam.x) / cam.scale;
    const py = (cy - cam.y) / cam.scale;
    const fit = Math.min(rect.width / current.width, rect.height / current.height);
    const maxScale = Math.max(fit * 12, (current.fullWidth / current.width) * 3);
    const nextScale = Math.min(maxScale, Math.max(fit * 0.5, cam.scale * factor));
    cameraRef.current = { scale: nextScale, x: cx - px * nextScale, y: cy - py * nextScale };
    applyCamera();
  }

  useEffect(() => {
    return () => {
      if (overviewUrl.current) URL.revokeObjectURL(overviewUrl.current);
      if (sharpUrl.current) URL.revokeObjectURL(sharpUrl.current);
    };
  }, []);

  useEffect(() => {
    if (!jobId || !band) return;
    let cancel = false;
    fittedRef.current = "";
    setPlaced(false);
    setNote("");
    const retired = [overviewUrl.current, sharpUrl.current].filter(Boolean);
    sharpUrl.current = "";
    overviewUrl.current = "";
    const show = (next: AccuracyView, sharp: boolean) => {
      if (sharp) {
        if (sharpUrl.current) URL.revokeObjectURL(sharpUrl.current);
        sharpUrl.current = next.url;
      } else if (sharpUrl.current) {
        URL.revokeObjectURL(next.url);
        return;
      } else {
        if (overviewUrl.current) URL.revokeObjectURL(overviewUrl.current);
        overviewUrl.current = next.url;
      }
      for (const url of retired) {
        if (url !== next.url) URL.revokeObjectURL(url);
      }
      retired.length = 0;
      imageRef.current = next;
      setImage(next);
    };
    api
      .accuracyOverview(jobId, band)
      .then((next) => {
        if (cancel) {
          URL.revokeObjectURL(next.url);
          return;
        }
        show(next, false);
      })
      .catch((error: Error) => {
        if (!cancel) setNote(error.message);
      });
    api
      .accuracyBrowse(jobId, band)
      .then((next) => {
        if (cancel) {
          URL.revokeObjectURL(next.url);
          return;
        }
        const prev = imageRef.current;
        const canvas = canvasRef.current;
        if (prev && canvas && prev.width > 0 && prev.fullWidth > 0 && fittedRef.current) {
          const rect = canvas.getBoundingClientRect();
          const cam = cameraRef.current;
          const cx = (rect.width / 2 - cam.x) / cam.scale;
          const cy = (rect.height / 2 - cam.y) / cam.scale;
          const fullCol = (cx / prev.width) * prev.fullWidth;
          const fullRow = (cy / prev.height) * prev.fullHeight;
          const screenPerFull = cam.scale * (prev.width / prev.fullWidth);
          const nextScale = screenPerFull * (next.fullWidth / next.width);
          const npx = (fullCol / next.fullWidth) * next.width;
          const npy = (fullRow / next.fullHeight) * next.height;
          cameraRef.current = { scale: nextScale, x: rect.width / 2 - npx * nextScale, y: rect.height / 2 - npy * nextScale };
          fittedRef.current = next.url;
        }
        show(next, true);
      })
      .catch(() => {
        /* 清晰图未就绪时继续用已载入的整幅图 */
      });
    return () => {
      cancel = true;
    };
  }, [jobId, band]);

  useLayoutEffect(() => {
    if (!image) return;
    if (fittedRef.current !== image.url) {
      fitImage(image);
      fittedRef.current = image.url;
    }
    applyCamera();
    setPlaced(true);
  }, [image]);

  useLayoutEffect(() => {
    applyCamera();
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    const onWheel = (event: WheelEvent) => {
      const current = imageRef.current;
      if (!current || current.width <= 0) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cam = cameraRef.current;
      const px = (event.clientX - rect.left - cam.x) / cam.scale;
      const py = (event.clientY - rect.top - cam.y) / cam.scale;
      const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
      const fit = Math.min(rect.width / current.width, rect.height / current.height);
      const maxScale = Math.max(fit * 12, (current.fullWidth / current.width) * 3);
      const nextScale = Math.min(maxScale, Math.max(fit * 0.5, cam.scale * factor));
      cameraRef.current = {
        scale: nextScale,
        x: event.clientX - rect.left - px * nextScale,
        y: event.clientY - rect.top - py * nextScale,
      };
      applyCamera();
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [image]);

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* 指针无法捕获时仍按移动距离拖动 */
    }
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    canvasRef.current?.classList.add("panning");
    const cam = cameraRef.current;
    cameraRef.current = { ...cam, x: cam.x + dx, y: cam.y + dy };
    drag.x = event.clientX;
    drag.y = event.clientY;
    applyCamera();
  }

  function onPointerUp() {
    dragRef.current = null;
    canvasRef.current?.classList.remove("panning");
  }

  return (
    <div className="ortho-stage">
      <div className="accuracy-stage">
        <div className="accuracy-overview">
          <Typography.Text type="secondary">整幅定位</Typography.Text>
          <div className="accuracy-frame">
            {image ? (
              <img
                src={image.url}
                alt="整幅正射"
                draggable={false}
                onClick={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  lookAt(
                    ((event.clientX - rect.left) / rect.width) * image.fullWidth,
                    ((event.clientY - rect.top) / rect.height) * image.fullHeight,
                  );
                }}
              />
            ) : (
              <span>…</span>
            )}
          </div>
        </div>
        <div
          ref={canvasRef}
          className="accuracy-canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          {image ? (
            <div
              ref={layerRef}
              className="accuracy-map"
              role="img"
              aria-label="拼图影像"
              style={{ width: image.width, height: image.height, visibility: placed ? "visible" : "hidden" }}
            >
              <img src={image.url} alt="拼图影像" draggable={false} width={image.width} height={image.height} />
            </div>
          ) : (
            <Typography.Text style={{ color: "#cbd5e1" }}>{note || "正在打开整幅拼图…"}</Typography.Text>
          )}
        </div>
      </div>
      <Space>
        <Button disabled={!image} onClick={() => nudge(0, 0.25)}>上</Button>
        <Button disabled={!image} onClick={() => nudge(0.25, 0)}>左</Button>
        <Button disabled={!image} onClick={() => nudge(-0.25, 0)}>右</Button>
        <Button disabled={!image} onClick={() => nudge(0, -0.25)}>下</Button>
        <Button disabled={!image} onClick={() => zoomBy(1.25)}>放大</Button>
        <Button disabled={!image} onClick={() => zoomBy(0.8)}>缩小</Button>
        <Typography.Text type="secondary">按住拖动，滚轮缩放。左侧小图用于跳转。</Typography.Text>
      </Space>
    </div>
  );
}
