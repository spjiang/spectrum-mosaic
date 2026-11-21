import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Modal, Space, Typography } from "antd";

/** 拼图影像那套查看：左侧小图跳转，画布里拖动、滚轮缩放。 */
export type StageImage = {
  url: string;
  width: number;
  height: number;
  fullWidth: number;
  fullHeight: number;
};

type Props = {
  image: StageImage | null;
  note?: string;
  /** 换任务或换图时重新适配，避免沿用上一张的缩放。 */
  resetKey?: string;
  alt?: string;
  overviewAlt?: string;
  emptyText?: string;
};

export default function ImageStage({
  image,
  note = "",
  resetKey = "",
  alt = "影像",
  overviewAlt = "整幅定位",
  emptyText = "正在打开影像…",
}: Props) {
  const [placed, setPlaced] = useState(false);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const cameraRef = useRef({ x: 0, y: 0, scale: 1 });
  const layerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<StageImage | null>(null);
  const fittedRef = useRef("");
  const fittedSize = useRef({ w: 0, h: 0 });
  const touchedRef = useRef(false);
  const resetSeen = useRef(resetKey);
  const imageUrlAtReset = useRef<string | null>(null);

  function applyCamera() {
    const layer = layerRef.current;
    const cam = cameraRef.current;
    if (!layer) return;
    layer.style.transform = `translate3d(${cam.x}px, ${cam.y}px, 0) scale(${cam.scale})`;
  }

  function fitImage(next: StageImage) {
    const canvas = canvasRef.current;
    if (!canvas || next.width <= 0 || next.height <= 0) return false;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return false;
    const scale = Math.min(rect.width / next.width, rect.height / next.height);
    cameraRef.current = {
      scale,
      x: (rect.width - next.width * scale) / 2,
      y: (rect.height - next.height * scale) / 2,
    };
    fittedSize.current = { w: rect.width, h: rect.height };
    applyCamera();
    return true;
  }

  function lookAt(col: number, row: number) {
    const current = imageRef.current;
    const canvas = canvasRef.current;
    if (!current || !canvas || current.width <= 0 || current.fullWidth <= 0) return;
    touchedRef.current = true;
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
    touchedRef.current = true;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    cameraRef.current = { ...cam, x: cam.x + dx * rect.width, y: cam.y + dy * rect.height };
    applyCamera();
  }

  function zoomBy(factor: number) {
    const canvas = canvasRef.current;
    const current = imageRef.current;
    if (!canvas || !current || current.width <= 0) return;
    touchedRef.current = true;
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

  useLayoutEffect(() => {
    if (resetSeen.current !== resetKey) {
      resetSeen.current = resetKey;
      fittedRef.current = "";
      fittedSize.current = { w: 0, h: 0 };
      touchedRef.current = false;
      imageUrlAtReset.current = image?.url ?? null;
      imageRef.current = null;
      setPlaced(false);
      return;
    }
    if (!image) {
      setPlaced(false);
      return;
    }
    if (imageUrlAtReset.current && image.url === imageUrlAtReset.current && !fittedRef.current) {
      setPlaced(false);
      return;
    }
    imageUrlAtReset.current = null;
    const prev = imageRef.current;
    const canvas = canvasRef.current;
    if (prev && prev.url !== image.url && prev.width > 0 && prev.fullWidth > 0 && fittedRef.current && canvas) {
      const rect = canvas.getBoundingClientRect();
      if (rect.width >= 8 && rect.height >= 8) {
        const cam = cameraRef.current;
        const cx = (rect.width / 2 - cam.x) / cam.scale;
        const cy = (rect.height / 2 - cam.y) / cam.scale;
        const fullCol = (cx / prev.width) * prev.fullWidth;
        const fullRow = (cy / prev.height) * prev.fullHeight;
        const screenPerFull = cam.scale * (prev.width / prev.fullWidth);
        const nextScale = screenPerFull * (image.fullWidth / image.width);
        const npx = (fullCol / image.fullWidth) * image.width;
        const npy = (fullRow / image.fullHeight) * image.height;
        cameraRef.current = { scale: nextScale, x: rect.width / 2 - npx * nextScale, y: rect.height / 2 - npy * nextScale };
        fittedRef.current = image.url;
        fittedSize.current = { w: rect.width, h: rect.height };
        imageRef.current = image;
        applyCamera();
        setPlaced(true);
        return;
      }
    }
    if (fittedRef.current !== image.url) {
      if (fitImage(image)) fittedRef.current = image.url;
    }
    imageRef.current = image;
    applyCamera();
    setPlaced(fittedRef.current === image.url);
  }, [image, resetKey]);

  useLayoutEffect(() => {
    applyCamera();
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const refit = () => {
      const current = imageRef.current;
      if (!current || touchedRef.current) return;
      if (imageUrlAtReset.current && current.url === imageUrlAtReset.current && !fittedRef.current) return;
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 8 || rect.height < 8) return;
      const grown =
        Math.abs(rect.width - fittedSize.current.w) > 24 || Math.abs(rect.height - fittedSize.current.h) > 24;
      if (fittedRef.current === current.url && !grown) return;
      if (!fitImage(current)) return;
      fittedRef.current = current.url;
      setPlaced(true);
    };
    refit();
    const observer = new ResizeObserver(refit);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [image?.url, resetKey]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    const onWheel = (event: WheelEvent) => {
      const current = imageRef.current;
      if (!current || current.width <= 0) return;
      event.preventDefault();
      touchedRef.current = true;
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
    touchedRef.current = true;
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
                alt={overviewAlt}
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
              aria-label={alt}
              style={{ width: image.width, height: image.height, visibility: placed ? "visible" : "hidden" }}
            >
              <img src={image.url} alt={alt} draggable={false} width={image.width} height={image.height} />
            </div>
          ) : (
            <Typography.Text style={{ color: "#cbd5e1" }}>{note || emptyText}</Typography.Text>
          )}
        </div>
      </div>
      <Space wrap>
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

export function ImageInspectModal({
  open,
  src,
  title,
  onClose,
  emptyText,
}: {
  open: boolean;
  src: string | null;
  title: string;
  onClose: () => void;
  emptyText?: string;
}) {
  const [image, setImage] = useState<StageImage | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!open || !src) {
      setImage(null);
      setNote("");
      return;
    }
    let cancel = false;
    setImage(null);
    setNote("");
    const img = new window.Image();
    img.onload = () => {
      if (cancel) return;
      const width = img.naturalWidth;
      const height = img.naturalHeight;
      if (width <= 0 || height <= 0) {
        setNote("影像打开失败");
        return;
      }
      setImage({ url: src, width, height, fullWidth: width, fullHeight: height });
    };
    img.onerror = () => {
      if (!cancel) setNote("影像打开失败");
    };
    img.src = src;
    return () => {
      cancel = true;
    };
  }, [open, src]);

  return (
    <Modal
      open={open}
      title={title}
      footer={null}
      onCancel={onClose}
      width="calc(100vw - 32px)"
      centered
      destroyOnClose
      className="image-inspect-modal"
      styles={{ body: { paddingTop: 12 } }}
      modalRender={(node) => (
        <div
          onClick={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
        >
          {node}
        </div>
      )}
    >
      <ImageStage
        image={image}
        note={note}
        resetKey={src || ""}
        alt={title}
        overviewAlt={title}
        emptyText={emptyText}
      />
    </Modal>
  );
}
