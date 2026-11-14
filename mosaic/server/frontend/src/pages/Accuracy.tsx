import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Card, Descriptions, Input, InputNumber, Select, Space, Table, Tag, Typography, message } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { api, type AccuracyPoint, type AccuracyState, type AccuracyView } from "../api";
import { useJobs } from "../jobsContext";
import { formatNo } from "../ids";

const RESULT_COLOR: Record<string, string> = {
  满足: "success",
  不满足: "error",
  未完成: "warning",
  未评估: "default",
};

function fmt(value: number | null | undefined, digits: number) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function textOf(value: number | null | undefined) {
  return value == null ? "" : String(value);
}

/** 精度验收：先在拼图上选点，列出拼图坐标，再填写野外 GPS，最后评估。 */
export default function AccuracyPage() {
  const { id: jobId } = useParams();
  const nav = useNavigate();
  const { jobs } = useJobs();
  const seq = jobs.find((item) => item.id === jobId)?.seq;
  const seqLabel = seq != null ? formatNo(seq) : "";
  const [state, setState] = useState<AccuracyState | null>(null);
  const [minCount, setMinCount] = useState(20);
  const [maxCount, setMaxCount] = useState(30);
  const [limit, setLimit] = useState(2);
  const [pointName, setPointName] = useState("");
  const [moving, setMoving] = useState(false);
  const [overview, setOverview] = useState<AccuracyView | null>(null);
  const [placed, setPlaced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, { x: string; y: string }>>({});
  const overviewUrl = useRef("");
  const sharpUrl = useRef("");
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const cameraRef = useRef({ x: 0, y: 0, scale: 1 });
  const layerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const overviewRef = useRef<AccuracyView | null>(null);
  const fittedRef = useRef("");

  function applyCamera() {
    const layer = layerRef.current;
    const cam = cameraRef.current;
    if (!layer) return;
    layer.style.transform = `translate3d(${cam.x}px, ${cam.y}px, 0) scale(${cam.scale})`;
    layer.style.setProperty("--s", String(cam.scale));
  }

  function fitImage(image: AccuracyView) {
    const canvas = canvasRef.current;
    if (!canvas || image.width <= 0 || image.height <= 0) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return;
    const scale = Math.min(rect.width / image.width, rect.height / image.height);
    cameraRef.current = {
      scale,
      x: (rect.width - image.width * scale) / 2,
      y: (rect.height - image.height * scale) / 2,
    };
    applyCamera();
  }

  function lookAt(col: number, row: number) {
    const image = overviewRef.current;
    const canvas = canvasRef.current;
    if (!image || !canvas || image.width <= 0 || image.fullWidth <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    const px = (col / image.fullWidth) * image.width;
    const py = (row / image.fullHeight) * image.height;
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
    const image = overviewRef.current;
    if (!canvas || !image || image.width <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const px = (cx - cam.x) / cam.scale;
    const py = (cy - cam.y) / cam.scale;
    const fit = Math.min(rect.width / image.width, rect.height / image.height);
    const maxScale = Math.max(fit * 12, (image.fullWidth / image.width) * 3);
    const nextScale = Math.min(maxScale, Math.max(fit * 0.5, cam.scale * factor));
    cameraRef.current = { scale: nextScale, x: cx - px * nextScale, y: cy - py * nextScale };
    applyCamera();
  }

  function remember(next: AccuracyState, focus = "") {
    setState(next);
    setMinCount(next.min_count);
    setMaxCount(next.max_count);
    setLimit(next.max_mean_px);
    setDrafts((current) => {
      const merged = { ...current };
      for (const point of next.points) {
        if (!merged[point.name]) {
          merged[point.name] = { x: textOf(point.gps_x), y: textOf(point.gps_y) };
        }
      }
      return merged;
    });
    const chosen = focus && next.points.some((point) => point.name === focus) ? focus : "";
    setPointName((current) => chosen || (next.points.some((point) => point.name === current) ? current : next.points.at(-1)?.name || ""));
    if (!chosen) return;
    const target = next.points.find((point) => point.name === chosen);
    if (target) lookAt(target.col, target.row);
  }

  useEffect(() => {
    if (!jobId) return;
    let cancel = false;
    api
      .accuracyState(jobId)
      .then((next) => {
        if (!cancel) remember(next);
      })
      .catch((error: Error) => message.error(error.message));
    return () => {
      cancel = true;
    };
  }, [jobId]);

  useEffect(() => {
    return () => {
      if (overviewUrl.current) URL.revokeObjectURL(overviewUrl.current);
      if (sharpUrl.current) URL.revokeObjectURL(sharpUrl.current);
    };
  }, []);

  const referenceId = state?.reference_band || "";

  useEffect(() => {
    if (!jobId || !referenceId) {
      overviewRef.current = null;
      setOverview(null);
      setPlaced(false);
      return;
    }
    let cancel = false;
    fittedRef.current = "";
    const retired = [overviewUrl.current, sharpUrl.current].filter(Boolean);
    sharpUrl.current = "";
    overviewUrl.current = "";
    const show = (next: AccuracyView, sharp: boolean) => {
      if (sharp) {
        if (sharpUrl.current) URL.revokeObjectURL(sharpUrl.current);
        sharpUrl.current = next.url;
      } else {
        if (sharpUrl.current) {
          URL.revokeObjectURL(next.url);
          return;
        }
        if (overviewUrl.current) URL.revokeObjectURL(overviewUrl.current);
        overviewUrl.current = next.url;
      }
      for (const url of retired) {
        if (url !== next.url) URL.revokeObjectURL(url);
      }
      retired.length = 0;
      overviewRef.current = next;
      setOverview(next);
    };
    api
      .accuracyOverview(jobId, referenceId)
      .then((next) => {
        if (cancel) {
          URL.revokeObjectURL(next.url);
          return;
        }
        show(next, false);
      })
      .catch((error: Error) => {
        if (!cancel) message.error(error.message);
      });
    api
      .accuracyBrowse(jobId, referenceId)
      .then((next) => {
        if (cancel) {
          URL.revokeObjectURL(next.url);
          return;
        }
        const prev = overviewRef.current;
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
  }, [jobId, referenceId]);

  useLayoutEffect(() => {
    if (!overview) return;
    if (fittedRef.current !== overview.url) {
      fitImage(overview);
      fittedRef.current = overview.url;
    }
    applyCamera();
    setPlaced(true);
  }, [overview]);

  useLayoutEffect(() => {
    applyCamera();
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !overview) return;
    const onWheel = (event: WheelEvent) => {
      const image = overviewRef.current;
      if (!image || image.width <= 0) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cam = cameraRef.current;
      const px = (event.clientX - rect.left - cam.x) / cam.scale;
      const py = (event.clientY - rect.top - cam.y) / cam.scale;
      const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
      const fit = Math.min(rect.width / image.width, rect.height / image.height);
      const maxScale = Math.max(fit * 12, (image.fullWidth / image.width) * 3);
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
  }, [overview]);

  async function saveConfig(nextMin = minCount, nextMax = maxCount, nextLimit = limit, nextReference = referenceId) {
    if (!jobId) return;
    try {
      remember(
        await api.accuracyConfig(jobId, {
          min_count: nextMin,
          max_count: nextMax,
          max_mean_px: nextLimit,
          reference_band: nextReference || undefined,
        }),
      );
    } catch (error: any) {
      message.error(error.message || String(error));
    }
  }

  async function onImageClick(col: number, row: number) {
    if (!jobId) return;
    setBusy(true);
    try {
      if (moving && pointName) {
        remember(await api.accuracyPick(jobId, { col, row, name: pointName }), pointName);
        setMoving(false);
      } else {
        const next = await api.accuracyPick(jobId, { col, row });
        remember(next, next.points.at(-1)?.name || "");
      }
    } catch (error: any) {
      message.error(error.message || String(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveGps(point: AccuracyPoint) {
    if (!jobId || !state) return;
    const draft = drafts[point.name] || { x: "", y: "" };
    const xText = draft.x.trim();
    const yText = draft.y.trim();
    try {
      remember(
        await api.accuracyGps(jobId, point.name, {
          x: xText === "" ? null : Number(xText),
          y: yText === "" ? null : Number(yText),
          z: null,
          crs: state.crs,
        }),
        point.name,
      );
    } catch (error: any) {
      message.error(error.message || String(error));
    }
  }

  function onMainPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* 指针无法捕获时，仍按移动距离判断拖动 */
    }
  }

  function onMainPointerMove(event: React.PointerEvent<HTMLDivElement>) {
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

  function onMainPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    dragRef.current = null;
    canvasRef.current?.classList.remove("panning");
    if (!drag || !overviewRef.current) return;
    if (drag.moved) return;
    const image = overviewRef.current;
    const canvas = canvasRef.current;
    if (!canvas || image.width <= 0) return;
    const rect = canvas.getBoundingClientRect();
    const cam = cameraRef.current;
    const px = (event.clientX - rect.left - cam.x) / cam.scale;
    const py = (event.clientY - rect.top - cam.y) / cam.scale;
    if (px < 0 || py < 0 || px > image.width || py > image.height) return;
    onImageClick((px / image.width) * image.fullWidth, (py / image.height) * image.fullHeight);
  }

  function pinStyle(col: number, row: number, frame: AccuracyView | null, full = false) {
    if (!frame || frame.width <= 0 || frame.height <= 0) return null;
    const left = full ? (col / frame.fullWidth) * 100 : ((col - frame.originCol) / frame.width) * 100;
    const top = full ? (row / frame.fullHeight) * 100 : ((row - frame.originRow) / frame.height) * 100;
    if (left < -2 || left > 102 || top < -2 || top > 102) return null;
    return { left: `${left}%`, top: `${top}%` };
  }

  const geographic = (state?.crs || "EPSG:4326") === "EPSG:4326";
  const xLabel = geographic ? "野外经度" : "野外东坐标";
  const yLabel = geographic ? "野外纬度" : "野外北坐标";
  const alertType = !state ? "info" : state.accepted ? "success" : state.evaluated ? "error" : "warning";
  const selected = state?.points.find((point) => point.name === pointName);

  return (
    <Card
      className="mosaic-panel accuracy-page"
      title={seqLabel ? `精度验收 ${seqLabel}` : "精度验收"}
      extra={<Button onClick={() => nav("/jobs")}>返回记录</Button>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          在基准波段正射上点击选择检测点。下方列表给出每个点在拼图上的坐标。填写对应的野外 GPS 坐标后，点击评估校验。像素误差等于拼图坐标与野外坐标的平面距离除以基准波段地面分辨率，各点取算术平均，须严格小于配置值。
        </Typography.Paragraph>
        <Space wrap>
          <span>最少点数</span>
          <InputNumber min={1} max={500} value={minCount} onChange={(value) => setMinCount(Number(value) || 1)} onBlur={() => saveConfig()} />
          <span>最多点数</span>
          <InputNumber min={1} max={500} value={maxCount} onChange={(value) => setMaxCount(Number(value) || 1)} onBlur={() => saveConfig()} />
          <span>平均位置误差上限</span>
          <InputNumber min={0.01} step={0.1} value={limit} onChange={(value) => setLimit(Number(value) || 0.01)} onBlur={() => saveConfig()} addonAfter="像素" />
          <span>基准波段</span>
          <Select
            style={{ minWidth: 120 }}
            value={referenceId || undefined}
            options={(state?.bands || []).map((item) => ({ value: item.id, label: item.label }))}
            onChange={(value) => saveConfig(minCount, maxCount, limit, value)}
          />
          <span>野外坐标系</span>
          <Select
            style={{ minWidth: 150 }}
            value={state?.crs || "EPSG:4326"}
            options={[
              { value: "EPSG:4326", label: "EPSG:4326" },
              ...(state?.ortho_epsg && state.ortho_epsg !== "EPSG:4326" ? [{ value: state.ortho_epsg, label: state.ortho_epsg }] : []),
            ]}
            onChange={async (value) => {
              if (!jobId || !state?.points.length) {
                setState((current) => (current ? { ...current, crs: value } : current));
                return;
              }
              const point = state.points[0];
              try {
                remember(await api.accuracyGps(jobId, point.name, { x: point.gps_x, y: point.gps_y, z: null, crs: value }), point.name);
              } catch (error: any) {
                message.error(error.message || String(error));
              }
            }}
          />
          <Button type={moving ? "default" : "primary"} onClick={() => setMoving(false)}>
            添加检测点
          </Button>
          <Button type={moving ? "primary" : "default"} disabled={!pointName} onClick={() => setMoving(true)}>
            移动选中点
          </Button>
          <Button
            type="primary"
            loading={busy}
            onClick={async () => {
              if (!jobId) return;
              setBusy(true);
              try {
                remember(await api.accuracyEvaluate(jobId), pointName);
              } catch (error: any) {
                message.error(error.message || String(error));
              } finally {
                setBusy(false);
              }
            }}
          >
            评估校验
          </Button>
        </Space>
        {state ? <Alert type={alertType} showIcon message={state.message} /> : null}
        <div className="accuracy-stage">
          <div className="accuracy-overview">
            <Typography.Text type="secondary">整幅定位</Typography.Text>
            <div className="accuracy-frame">
              {overview ? (
                <img
                  src={overview.url}
                  alt="整幅正射"
                  draggable={false}
                  onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    const col = ((event.clientX - rect.left) / rect.width) * overview.fullWidth;
                    const row = ((event.clientY - rect.top) / rect.height) * overview.fullHeight;
                    lookAt(col, row);
                  }}
                />
              ) : (
                <span>…</span>
              )}
              {(state?.points || []).map((point) => {
                const style = pinStyle(point.col, point.row, overview, true);
                return style ? <span key={point.name} className="accuracy-pin mark" style={style} /> : null;
              })}
            </div>
          </div>
          <div
            ref={canvasRef}
            className="accuracy-canvas"
            onPointerDown={onMainPointerDown}
            onPointerMove={onMainPointerMove}
            onPointerUp={onMainPointerUp}
            onPointerCancel={onMainPointerUp}
          >
            {overview ? (
              <div
                ref={layerRef}
                className="accuracy-map"
                role="img"
                aria-label="基准波段大图"
                style={{ width: overview.width, height: overview.height, visibility: placed ? "visible" : "hidden" }}
              >
                <img src={overview.url} alt="基准波段" draggable={false} width={overview.width} height={overview.height} />
                {(state?.points || []).map((point) => {
                  const style = pinStyle(point.col, point.row, overview, true);
                  return style ? <span key={point.name} className="accuracy-pin mark" style={style} title={point.name} /> : null;
                })}
                {selected?.gps_col != null && selected.gps_row != null && pinStyle(selected.gps_col, selected.gps_row, overview, true) ? (
                  <span className="accuracy-pin expect" style={pinStyle(selected.gps_col, selected.gps_row, overview, true) || undefined} title="野外坐标" />
                ) : null}
              </div>
            ) : (
              <Typography.Text style={{ color: "#cbd5e1" }}>正在打开整幅正射…</Typography.Text>
            )}
          </div>
        </div>
        <Space>
          <Button disabled={!overview} onClick={() => nudge(0, 0.25)}>
            上
          </Button>
          <Button disabled={!overview} onClick={() => nudge(0.25, 0)}>
            左
          </Button>
          <Button disabled={!overview} onClick={() => nudge(-0.25, 0)}>
            右
          </Button>
          <Button disabled={!overview} onClick={() => nudge(0, -0.25)}>
            下
          </Button>
          <Button disabled={!overview} onClick={() => zoomBy(1.25)}>
            放大
          </Button>
          <Button disabled={!overview} onClick={() => zoomBy(0.8)}>
            缩小
          </Button>
          <Typography.Text type="secondary">
            {moving
              ? "下一次点击将移动选中点。拖动和滚轮只在已载入的图上移动，不再重新取图。"
              : "单击大图添加检测点。按住拖动、滚轮缩放都在已载入的整幅图上完成。左侧小图只用于跳转。蓝点是拼图上的选点。"}
          </Typography.Text>
        </Space>
        <Table
          size="small"
          pagination={false}
          rowKey="name"
          dataSource={state?.points || []}
          onRow={(point) => ({
            onClick: () => {
              setPointName(point.name);
              lookAt(point.col, point.row);
            },
          })}
          rowClassName={(point) => (point.name === pointName ? "accuracy-row-active" : "")}
          columns={[
            { title: "点名", dataIndex: "name", width: 72 },
            {
              title: "拼图坐标",
              render: (_, point) => (
                <div>
                  <div>{fmt(point.lon, 8)}，{fmt(point.lat, 8)}</div>
                  <Typography.Text type="secondary">东 {fmt(point.east, 3)}　北 {fmt(point.north, 3)}</Typography.Text>
                </div>
              ),
            },
            {
              title: xLabel,
              width: 150,
              render: (_, point) => (
                <Input
                  value={drafts[point.name]?.x ?? ""}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => setDrafts((current) => ({ ...current, [point.name]: { ...(current[point.name] || { x: "", y: "" }), x: event.target.value } }))}
                  onBlur={() => saveGps(point)}
                />
              ),
            },
            {
              title: yLabel,
              width: 150,
              render: (_, point) => (
                <Input
                  value={drafts[point.name]?.y ?? ""}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => setDrafts((current) => ({ ...current, [point.name]: { ...(current[point.name] || { x: "", y: "" }), y: event.target.value } }))}
                  onBlur={() => saveGps(point)}
                />
              ),
            },
            { title: "平面 m", width: 90, render: (_, point) => fmt(point.dist_m, 3) },
            { title: "像素", width: 80, render: (_, point) => fmt(point.err_px, 2) },
            {
              title: "",
              width: 72,
              render: (_, point) => (
                <Button
                  type="link"
                  danger
                  onClick={async (event) => {
                    event.stopPropagation();
                    if (!jobId) return;
                    try {
                      remember(await api.accuracyDeletePoint(jobId, point.name));
                    } catch (error: any) {
                      message.error(error.message || String(error));
                    }
                  }}
                >
                  删除
                </Button>
              ),
            },
          ]}
        />
        <Descriptions
          size="small"
          column={1}
          items={(state?.clauses || []).map((clause) => ({
            key: clause.no,
            label: `第 ${clause.no} 条`,
            children: (
              <div>
                <div>
                  {clause.text} <Tag color={RESULT_COLOR[clause.result] || "default"}>{clause.result}</Tag>
                </div>
                <Typography.Text type="secondary">{clause.detail}</Typography.Text>
              </div>
            ),
          }))}
        />
      </Space>
    </Card>
  );
}
