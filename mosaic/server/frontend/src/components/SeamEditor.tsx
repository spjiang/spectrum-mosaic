import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Button, Modal, Spin, Tooltip, Typography, message } from "antd";
import { QuestionCircleOutlined, ZoomInOutlined } from "@ant-design/icons";
import { api } from "../api";

type Pt = [number, number];
type Region = { image_id: number; points: Pt[]; origin_id?: number; edited?: boolean; drawn?: boolean };
type Candidate = { image_id: number; name: string; center_offset: number };

function cropUrl(image: HTMLImageElement, points: Pt[]) {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.max(0, Math.floor(Math.min(...xs)));
  const y0 = Math.max(0, Math.floor(Math.min(...ys)));
  const x1 = Math.min(image.naturalWidth, Math.ceil(Math.max(...xs)));
  const y1 = Math.min(image.naturalHeight, Math.ceil(Math.max(...ys)));
  const w = x1 - x0;
  const h = y1 - y0;
  if (w < 2 || h < 2) return "";
  const canvas = document.createElement("canvas");
  const scale = Math.min(520 / w, 200 / h, 3);
  canvas.width = Math.max(2, Math.round(w * scale));
  canvas.height = Math.max(2, Math.round(h * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  ctx.drawImage(image, x0, y0, w, h, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.82);
}

function colorFor(id: number, alpha = 0.28) {
  const hue = (id * 47) % 360;
  return `hsla(${hue}, 72%, 46%, ${alpha})`;
}

function polygonArea(points: Pt[]) {
  let sum = 0;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    sum += points[j][0] * points[i][1] - points[i][0] * points[j][1];
  }
  return Math.abs(sum) / 2;
}

function centroid(points: Pt[]): Pt {
  return [
    points.reduce((sum, p) => sum + p[0], 0) / points.length,
    points.reduce((sum, p) => sum + p[1], 0) / points.length,
  ];
}

function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt) {
  const cross = (p: Pt, q: Pt, r: Pt) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const d1 = cross(a, b, c);
  const d2 = cross(a, b, d);
  const d3 = cross(c, d, a);
  const d4 = cross(c, d, b);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

function overlaps(a: Pt[], b: Pt[]) {
  if (a.length < 3 || b.length < 3) return false;
  if (a.some((p) => inside(b, p[0], p[1])) || b.some((p) => inside(a, p[0], p[1]))) return true;
  for (let i = 0; i < a.length; i += 1) {
    const a2 = a[(i + 1) % a.length];
    for (let j = 0; j < b.length; j += 1) {
      if (segmentsCross(a[i], a2, b[j], b[(j + 1) % b.length])) return true;
    }
  }
  return false;
}

function inside(points: Pt[], x: number, y: number) {
  let hit = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i];
    const [xj, yj] = points[j];
    const cross = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-9) + xi;
    if (cross) hit = !hit;
  }
  return hit;
}

function ToolHelp({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip title={text} placement="top">
      <button type="button" className="seam-tool-help" aria-label={`${label}说明`} onClick={(ev) => ev.preventDefault()}>
        <QuestionCircleOutlined />
      </button>
    </Tooltip>
  );
}

function eventPoint(canvas: HTMLCanvasElement, ev: MouseEvent<HTMLCanvasElement>): Pt {
  const rect = canvas.getBoundingClientRect();
  return [
    ((ev.clientX - rect.left) / rect.width) * canvas.width,
    ((ev.clientY - rect.top) / rect.height) * canvas.height,
  ];
}

export default function SeamEditor({
  jobId,
  disabled,
  onChanged,
  version,
  readOnly = false,
  savedEdit = false,
}: {
  jobId: string;
  disabled: boolean;
  onChanged?: (job: any) => void;
  version?: string;
  readOnly?: boolean;
  savedEdit?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [fit, setFit] = useState({ w: 0, h: 0 });
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<Pt[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [assign, setAssign] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [preview, setPreview] = useState<Candidate | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const load = useCallback(async () => {
    setError("");
    setImage(null);
    const [map, blob] = await Promise.all([api.seamMap(jobId, version), api.seamPreview(jobId, version)]);
    const url = URL.createObjectURL(blob);
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("正射预览加载失败"));
      img.src = url;
    });
    setImage(img);
    setRegions(map.features.map((f) => ({ image_id: f.image_id, origin_id: f.image_id, points: f.points as Pt[] })));
    setSelected(null);
    setDraft([]);
    setDrawing(false);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [jobId, version]);

  useEffect(() => {
    let cancel = false;
    load().catch((e: any) => {
      if (!cancel) setError(e.message || String(e));
    });
    return () => {
      cancel = true;
    };
  }, [load]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0);
    const paint = (index: number, mode: "plain" | "kept" | "selected") => {
      const region = regions[index];
      if (!region || region.points.length < 3) return;
      ctx.beginPath();
      region.points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      ctx.closePath();
      if (mode === "selected") {
        ctx.strokeStyle = "#d4380d";
        ctx.lineWidth = 3;
        ctx.fillStyle = colorFor(region.image_id, 0.45);
        ctx.fill();
      } else if (mode === "kept") {
        ctx.strokeStyle = "#d4380d";
        ctx.lineWidth = 2;
        ctx.fillStyle = colorFor(region.image_id, 0.18);
        ctx.fill();
      } else {
        ctx.strokeStyle = "rgba(255,255,255,0.92)";
        ctx.lineWidth = 1.25;
      }
      ctx.stroke();
    };
    regions.forEach((region, index) => {
      if (index !== selected) paint(index, region.drawn || region.edited ? "kept" : "plain");
    });
    if (hover != null && hover !== selected) {
      const region = regions[hover];
      if (region && region.points.length >= 3) {
        ctx.beginPath();
        region.points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
        ctx.closePath();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
    }
    if (selected != null) paint(selected, "selected");
    if (draft.length) {
      ctx.beginPath();
      draft.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      if (draft.length >= 3) ctx.closePath();
      if (assign != null && draft.length >= 3) {
        ctx.fillStyle = colorFor(assign, 0.45);
        ctx.fill();
      }
      ctx.strokeStyle = "#d4380d";
      ctx.lineWidth = 3;
      ctx.stroke();
      draft.forEach(([x, y]) => {
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = "#d4380d";
        ctx.fill();
      });
    }
  }, [image, regions, selected, draft, assign, drawing, hover]);

  const current = selected != null ? regions[selected] : null;
  const changed = regions.filter((r) => r.edited || r.drawn);
  const coverage = useMemo(() => {
    const drawn = !drawing && current?.drawn && current.points.length >= 3 ? current.points : null;
    if (drawn) {
      let blocks = 0;
      for (const region of regions) {
        if (region.drawn || region.points.length < 3 || !overlaps(drawn, region.points)) continue;
        blocks += 1;
      }
      return { samples: [centroid(drawn)], blocks, polygon: drawn };
    }
    const pts = drawing && draft.length > 0 ? draft : current?.points;
    if (!pts || pts.length === 0) return { samples: [] as Pt[], blocks: 0, polygon: null as Pt[] | null };
    return { samples: [centroid(pts)], blocks: 0, polygon: null as Pt[] | null };
  }, [drawing, draft, current, regions]);
  const focus = coverage.samples[0] || null;
  const sampleKey = coverage.polygon
    ? `poly:${coverage.polygon.map((point) => `${point[0].toFixed(0)},${point[1].toFixed(0)}`).join(";")}`
    : coverage.samples.map((point) => `${point[0].toFixed(0)},${point[1].toFixed(0)}`).join(";");
  useEffect(() => {
    if (readOnly || (!coverage.polygon && coverage.samples.length === 0)) {
      setCandidates([]);
      return undefined;
    }
    let cancel = false;
    const request = coverage.polygon
      ? api.seamCandidatesMany(jobId, coverage.polygon)
      : api.seamCandidates(jobId, coverage.samples[0][0], coverage.samples[0][1]);
    request
      .then((payload) => {
        if (!cancel) setCandidates(payload.images || []);
      })
      .catch(() => {
        if (!cancel) setCandidates([]);
      });
    return () => {
      cancel = true;
    };
  }, [jobId, readOnly, sampleKey, coverage.samples]);
  const [thumbs, setThumbs] = useState<Record<number, string>>({});
  const thumbIds = candidates.map((row) => row.image_id).join(",");
  useEffect(() => {
    if (readOnly || candidates.length === 0) return undefined;
    let cancel = false;
    const created: string[] = [];
    let cursor = 0;
    const worker = async () => {
      while (!cancel) {
        const row = candidates[cursor];
        cursor += 1;
        if (!row) return;
        try {
          const blob = await api.seamImage(jobId, row.image_id);
          if (cancel) return;
          const url = URL.createObjectURL(blob);
          created.push(url);
          setThumbs((prev) => ({ ...prev, [row.image_id]: url }));
        } catch {
          /* 缺文件时格子留空 */
        }
      }
    };
    const pool = Array.from({ length: Math.min(3, candidates.length) }, () => worker());
    return () => {
      cancel = true;
      void pool;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [jobId, readOnly, thumbIds, candidates]);
  const crop = useMemo(() => {
    if (!image) return "";
    const pts = drawing && draft.length >= 3 ? draft : current?.points;
    return pts && pts.length >= 3 ? cropUrl(image, pts) : "";
  }, [image, current, drawing, draft]);
  const hero = candidates.find((row) => row.image_id === assign) || candidates[0] || null;

  function hitIndex(x: number, y: number) {
    const hits: number[] = [];
    for (let i = 0; i < regions.length; i += 1) {
      if (inside(regions[i].points, x, y)) hits.push(i);
    }
    const smallest = (indexes: number[]) => {
      let best = -1;
      let bestArea = Number.POSITIVE_INFINITY;
      for (const i of indexes) {
        const area = polygonArea(regions[i].points);
        if (area < bestArea) {
          best = i;
          bestArea = area;
        }
      }
      return best;
    };
    // 自己画的范围盖在自动色块上。再点这块时要选中范围，不能被里面的小色块抢走。
    const drawn = hits.filter((i) => regions[i].drawn);
    if (drawn.length) return smallest(drawn);
    return smallest(hits);
  }

  zoomRef.current = zoom;
  panRef.current = pan;

  function clampPan(x: number, y: number, z: number) {
    const stage = stageRef.current;
    const el = viewRef.current;
    if (!stage || !el || z <= 1) return { x: 0, y: 0 };
    const layoutLeft = (stage.clientWidth - el.offsetWidth) / 2;
    const layoutTop = (stage.clientHeight - el.offsetHeight) / 2;
    const minX = 80 - layoutLeft - el.offsetWidth * z;
    const maxX = stage.clientWidth - 80 - layoutLeft;
    const minY = 80 - layoutTop - el.offsetHeight * z;
    const maxY = stage.clientHeight - 80 - layoutTop;
    return {
      x: Math.min(maxX, Math.max(minX, x)),
      y: Math.min(maxY, Math.max(minY, y)),
    };
  }

  function zoomAt(clientX: number, clientY: number, factor: number) {
    const el = viewRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const current = zoomRef.current;
    const next = Math.min(8, Math.max(1, current * factor));
    if (next === 1) {
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }
    const ratio = next / current;
    const ox = clientX - rect.left;
    const oy = clientY - rect.top;
    const shifted = clampPan(panRef.current.x + clientX - ox * ratio - rect.left, panRef.current.y + clientY - oy * ratio - rect.top, next);
    setZoom(next);
    setPan(shifted);
  }

  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage || !image) return undefined;
    const apply = () => {
      const scale = Math.min(stage.clientWidth / image.naturalWidth, stage.clientHeight / image.naturalHeight);
      setFit({
        w: Math.max(1, Math.floor(image.naturalWidth * scale)),
        h: Math.max(1, Math.floor(image.naturalHeight * scale)),
      });
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [image]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !image) return undefined;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      zoomAt(ev.clientX, ev.clientY, ev.deltaY < 0 ? 1.18 : 1 / 1.18);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [image]);

  function pick(ev: MouseEvent<HTMLCanvasElement>) {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas || disabled) return;
    const [x, y] = eventPoint(canvas, ev);
    if (drawing) {
      setDraft((pts) => [...pts, [x, y]]);
      return;
    }
    const best = hitIndex(x, y);
    if (best < 0) return;
    const region = regions[best];
    if (best === selected && !region.drawn) {
      setSelected(null);
      setAssign(null);
      return;
    }
    setSelected(best);
    setAssign(region.edited || region.drawn ? region.image_id : null);
  }

  function move(ev: MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || disabled || drawing) return;
    const [x, y] = eventPoint(canvas, ev);
    const next = hitIndex(x, y);
    const hoverIndex = next >= 0 ? next : null;
    setHover((prev) => (prev === hoverIndex ? prev : hoverIndex));
  }

  async function openPreview(row: Candidate) {
    setPreview(row);
    setPreviewLoading(true);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return "";
    });
    try {
      const blob = await api.seamImage(jobId, row.image_id, 1600);
      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
    } catch (e: any) {
      message.error(e.message || "影像放大失败");
      setPreview(null);
    } finally {
      setPreviewLoading(false);
    }
  }

  function bindImage(id: number) {
    setAssign(id);
    if (selected != null && !drawing) {
      setRegions((list) => list.map((r, i) => (i === selected ? { ...r, image_id: id, edited: true } : r)));
    }
  }

  function closeDraft() {
    const imageId = assign ?? candidates[0]?.image_id;
    if (draft.length < 3 || imageId == null) {
      message.warning("先把范围点出来，再点一张能拍到这里的照片");
      return;
    }
    setAssign(imageId);
    setRegions((list) => [...list, { image_id: imageId, points: draft, drawn: true }]);
    setDraft([]);
    setDrawing(false);
    setSelected(regions.length);
  }

  function undoPoint() {
    setDraft((pts) => pts.slice(0, -1));
  }

  function removeSelected() {
    if (selected == null) {
      message.warning("先点中要删的那一块");
      return;
    }
    const region = regions[selected];
    if (region?.drawn) {
      setRegions((list) => list.filter((_, index) => index !== selected));
      setSelected(null);
      setAssign(null);
      setHover(null);
      return;
    }
    if (region?.edited) {
      const origin = region.origin_id ?? region.image_id;
      setRegions((list) => list.map((item, index) => (index === selected ? { ...item, image_id: origin, edited: false } : item)));
      setAssign(null);
      return;
    }
    message.warning("自动拼出来的色块不能删。画好的范围点中后可以删除。");
  }

  return (
    <div className={readOnly ? "seam-workspace is-readonly" : "seam-workspace"}>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      {!error && !image ? <Typography.Text type="secondary">正在生成正射预览和拼接线…</Typography.Text> : null}
      {image ? (
        <>
          <div
            className="seam-stage"
            ref={stageRef}
            onPointerDown={(ev) => {
              suppressClick.current = false;
              if (drawing || ev.button !== 0 || zoom <= 1) return;
              if ((ev.target as HTMLElement).closest("button")) return;
              dragRef.current = { x: ev.clientX, y: ev.clientY, panX: pan.x, panY: pan.y, moved: false };
              ev.currentTarget.setPointerCapture(ev.pointerId);
            }}
            onPointerMove={(ev) => {
              const drag = dragRef.current;
              if (!drag) return;
              const dx = ev.clientX - drag.x;
              const dy = ev.clientY - drag.y;
              if (Math.hypot(dx, dy) > 4) drag.moved = true;
              if (drag.moved) setPan(clampPan(drag.panX + dx, drag.panY + dy, zoom));
            }}
            onPointerUp={() => {
              suppressClick.current = Boolean(dragRef.current?.moved);
              dragRef.current = null;
            }}
          >
            <div
              className="seam-viewport"
              ref={viewRef}
              style={{
                width: fit.w || undefined,
                height: fit.h || undefined,
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              }}
            >
              <canvas
                ref={canvasRef}
                onClick={readOnly ? undefined : pick}
                onMouseMove={readOnly ? undefined : move}
                onMouseLeave={readOnly ? undefined : () => setHover(null)}
                style={{ cursor: readOnly ? "default" : drawing ? "crosshair" : zoom > 1 ? "grab" : "pointer" }}
              />
            </div>
            <div className="seam-zoom">
              <Button onClick={() => {
                const stage = stageRef.current;
                if (!stage) return;
                const box = stage.getBoundingClientRect();
                zoomAt(box.left + box.width / 2, box.top + box.height / 2, 1.25);
              }}
              >
                放大
              </Button>
              <span className="seam-zoom-label">{Math.round(zoom * 100)}%</span>
              <Button
                disabled={zoom <= 1}
                onClick={() => {
                  const stage = stageRef.current;
                  if (!stage) return;
                  const box = stage.getBoundingClientRect();
                  zoomAt(box.left + box.width / 2, box.top + box.height / 2, 1 / 1.25);
                }}
              >
                缩小
              </Button>
              <Button
                disabled={zoom <= 1 && pan.x === 0 && pan.y === 0}
                onClick={() => {
                  setZoom(1);
                  setPan({ x: 0, y: 0 });
                }}
              >
                适应
              </Button>
            </div>
            {readOnly ? null : (
              <div className="seam-toolbar">
                <span className="seam-tool">
                  <Button
                    disabled={disabled}
                    onClick={() => {
                      setDraft([]);
                      setDrawing((on) => !on);
                    }}
                  >
                    {drawing ? "取消绘制" : "绘制范围"}
                  </Button>
                  <ToolHelp
                    label={drawing ? "取消绘制" : "绘制范围"}
                    text={drawing ? "放弃当前还没闭合的范围。" : "在图上依次点出一块地面，用来指定这块改用哪张照片。画好后可以再次点中这块范围。"}
                  />
                </span>
                {drawing ? (
                  <span className="seam-tool">
                    <Button type="primary" onClick={closeDraft} disabled={draft.length < 3}>
                      闭合
                    </Button>
                    <ToolHelp label="闭合" text="至少点 3 下后，把这些点收成一块范围，并使用右侧选中的照片。" />
                  </span>
                ) : null}
                {drawing && draft.length > 0 ? (
                  <span className="seam-tool">
                    <Button disabled={disabled} onClick={undoPoint}>
                      撤销一点
                    </Button>
                    <ToolHelp label="撤销一点" text="去掉刚才点的最后一个顶点。" />
                  </span>
                ) : null}
                <span className="seam-tool">
                  <Button
                    danger
                    disabled={disabled || !current || (!current.drawn && !current.edited)}
                    onClick={removeSelected}
                  >
                    {current?.edited && !current.drawn ? "撤销修改" : "删除这块"}
                  </Button>
                  <ToolHelp
                    label={current?.edited && !current.drawn ? "撤销修改" : "删除这块"}
                    text={
                      current?.edited && !current.drawn
                        ? "把这块改回原来自动选中的照片。"
                        : "去掉自己画出来的范围。自动拼出来的色块不能删。"
                    }
                  />
                </span>
                <span className="seam-tool">
                  <Button
                    type="primary"
                    loading={busy}
                    disabled={disabled || changed.length === 0}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        const next = await api.reseamJob(jobId, {
                          regions: changed.map((r) => ({ image_id: r.image_id, points: r.points })),
                        });
                        message.success(next.message || "已开始按图上的范围重算拼接线");
                        onChanged?.(next);
                      } catch (e: any) {
                        message.error(e.message || String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {changed.length > 0 ? `重算 ${changed.length} 块` : "重算"}
                  </Button>
                  <ToolHelp label="重算" text="按你刚改的这块重新出图。没改过不能点。" />
                </span>
                <span className="seam-tool">
                  <Button
                    disabled={disabled || busy || (changed.length === 0 && !savedEdit)}
                    onClick={async () => {
                      if (!savedEdit) {
                        setRegions((list) =>
                          list
                            .filter((item) => !item.drawn)
                            .map((item) => ({ ...item, image_id: item.origin_id ?? item.image_id, edited: false })),
                        );
                        setSelected(null);
                        setAssign(null);
                        setDraft([]);
                        setDrawing(false);
                        message.success("已撤销未提交的修改。当前成果仍是自动拼接线，没有重新计算。");
                        return;
                      }
                      setBusy(true);
                      try {
                        const next = await api.reseamJob(jobId, { clear_edits: true });
                        message.success(next.message || "已放弃手工修改，按软件自己的拼接线重新出图");
                        onChanged?.(next);
                      } catch (e: any) {
                        message.error(e.message || String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    放弃修改
                  </Button>
                  <ToolHelp
                    label="放弃修改"
                    text="丢掉这次手工改法。只在图上改过、还没出图时，只把选择撤销，不重新计算。已经按手工范围出过图时，才会再出一版软件自己选的结果。"
                  />
                </span>
              </div>
            )}
          </div>
          {readOnly ? null : (
            <aside className="seam-inspector">
              {!focus ? (
                <div className="seam-inspector-empty">点图上的一块，或绘制一块范围。这里会显示这块地面，以及能拍到它的原始照片。</div>
              ) : (
                <>
                  {crop ? (
                    <figure className="seam-crop">
                      <img src={crop} alt="选中区域" />
                      <figcaption>选中的地面</figcaption>
                    </figure>
                  ) : (
                    <div className="seam-inspector-empty">继续点，至少 3 下才能闭合这块范围。</div>
                  )}
                  {hero ? (
                    <figure className="seam-hero">
                      {thumbs[hero.image_id] ? <img src={thumbs[hero.image_id]} alt={hero.name} /> : <div className="seam-photo-empty" />}
                      <button type="button" className="seam-photo-zoom" aria-label={`放大${hero.name}`} onClick={() => openPreview(hero)}>
                        <ZoomInOutlined />
                      </button>
                      <figcaption>
                        {hero.name}
                        {hero.image_id === assign ? " · 已用于这块" : " · 最正对这块"}
                      </figcaption>
                    </figure>
                  ) : (
                    <div className="seam-inspector-empty">这块地面没有彩色照片能拍到。</div>
                  )}
                  {coverage.polygon ? (
                    <div className="seam-inspector-empty">
                      这块范围的重叠影像，共 {candidates.length} 张。
                    </div>
                  ) : null}
                  <div className="seam-photo-grid">
                    {candidates.map((row) => (
                      <div key={row.image_id} className={row.image_id === assign ? "seam-photo-card is-on" : "seam-photo-card"}>
                        <button type="button" disabled={disabled} onClick={() => bindImage(row.image_id)}>
                          {thumbs[row.image_id] ? <img src={thumbs[row.image_id]} alt="" /> : <span className="seam-photo-empty" />}
                          <span title={row.name}>{row.name}</span>
                        </button>
                        <button
                          type="button"
                          className="seam-photo-zoom"
                          aria-label={`放大${row.name}`}
                          onClick={() => openPreview(row)}
                        >
                          <ZoomInOutlined />
                        </button>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </aside>
          )}
        </>
      ) : null}
      <Modal
        title={preview ? preview.name : "影像"}
        open={preview != null}
        footer={null}
        width={880}
        destroyOnClose
        onCancel={() => {
          setPreview(null);
          setPreviewUrl((prev) => {
            if (prev) URL.revokeObjectURL(prev);
            return "";
          });
        }}
      >
        <div className="seam-photo-modal">
          {previewLoading ? <Spin /> : null}
          {previewUrl ? <img src={previewUrl} alt={preview ? preview.name : ""} /> : null}
        </div>
      </Modal>
    </div>
  );
}
