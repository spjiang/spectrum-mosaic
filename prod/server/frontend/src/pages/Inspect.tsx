import { useEffect, useRef, useState, type MouseEvent } from "react";
import {
  Alert,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Progress,
  Space,
  Spin,
  Table,
  Tabs,
  Typography,
  Upload,
  message,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { api, type InspectMeta, type InspectPixel } from "../api";
import ErrorBoundary from "../components/ErrorBoundary";

function fmt(v: unknown): string {
  try {
    if (v == null || v === "") return "—";
    if (typeof v === "number") {
      if (!Number.isFinite(v)) return "—";
      if (Number.isInteger(v)) return String(v);
      return Math.abs(v) >= 1000 || Math.abs(v) < 0.001 ? v.toPrecision(6) : v.toFixed(4);
    }
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  } catch {
    return "—";
  }
}

function bytes(n: number | undefined) {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function layerStem(filename: string) {
  return filename.replace(/\.(tif|tiff|jpg|jpeg)$/i, "");
}

function asNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
}

function formatDecimal(v: unknown, decimals: number, enabled: boolean): string {
  const n = asNumber(v);
  if (n == null) return v == null || v === "" ? "—" : String(v);
  if (!enabled) return Number.isInteger(n) ? String(n) : String(n);
  return n.toFixed(decimals);
}

function enviImageXY(col: number, row: number) {
  return { x: col + 0.5, y: -(row + 0.5) };
}

function parseCoordinate(text: string, meta: InspectMeta): { col: number; row: number } | null {
  const m = text.trim().match(/^([+-]?\d+(?:\.\d+)?)\s*[,，\s]\s*([+-]?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const a = Number(m[1]);
  const b = Number(m[2]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  if (b <= 0 && a >= -0.5 && a < meta.width + 1 && Math.abs(b) < meta.height + 1) {
    return {
      col: Math.min(meta.width - 1, Math.max(0, Math.floor(a))),
      row: Math.min(meta.height - 1, Math.max(0, Math.floor(Math.abs(b)))),
    };
  }
  const geo = meta.geotransform;
  if (geo && (Math.abs(a) > meta.width + 8 || Math.abs(b) > meta.height + 8)) {
    const col = Math.floor((a - geo.origin_x) / geo.pixel_w);
    const row = Math.floor((b - geo.origin_y) / geo.pixel_h);
    return {
      col: Math.min(meta.width - 1, Math.max(0, col)),
      row: Math.min(meta.height - 1, Math.max(0, row)),
    };
  }
  return {
    col: Math.min(meta.width - 1, Math.max(0, Math.floor(a))),
    row: Math.min(meta.height - 1, Math.max(0, Math.floor(b))),
  };
}

type ProbePoint = {
  key: string;
  index: number;
  layer: string;
  band: string;
  wavelength_nm: number | null;
  value: number | string | null;
  row: number;
  col: number;
};

function buildPoints(meta: InspectMeta, pixel: InspectPixel): ProbePoint[] {
  const stem = layerStem(meta.filename);
  return (pixel.bands || pixel.values.map((value, i) => ({ name: meta.bands[i]?.name || `波段 ${i + 1}`, value, wavelength_nm: meta.bands[i]?.wavelength_nm ?? null })))
    .map((ch, i) => ({
      key: `${i}`,
      index: i + 1,
      layer: meta.count > 1 ? `${stem} / ${ch.name}` : stem,
      band: ch.name,
      wavelength_nm: ch.wavelength_nm ?? meta.bands[i]?.wavelength_nm ?? null,
      value: ch.value ?? pixel.values[i] ?? null,
      row: pixel.row,
      col: pixel.col,
    }));
}

function ProbeGraph({
  points,
  yMin,
  yMax,
}: {
  points: ProbePoint[];
  yMin?: number | null;
  yMax?: number | null;
}) {
  const nums = points
    .map((p) => ({ ...p, n: asNumber(p.value) }))
    .filter((p): p is ProbePoint & { n: number } => p.n != null);
  if (!nums.length) {
    return <Empty description="当前坐标没有可绘制的数值" />;
  }
  const useWl = nums.every((p) => p.wavelength_nm != null);
  const xs = nums.map((p, i) => (useWl ? Number(p.wavelength_nm) : p.index));
  const ys = nums.map((p) => p.n);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const autoLo = Math.min(...ys);
  const autoHi = Math.max(...ys);
  const lo = yMin == null || !Number.isFinite(yMin) ? (autoLo === autoHi ? autoLo - 1 : autoLo) : yMin;
  const hi = yMax == null || !Number.isFinite(yMax) ? (autoLo === autoHi ? autoHi + 1 : autoHi) : yMax;
  const padL = 48;
  const padR = 16;
  const padT = 16;
  const padB = 36;
  const w = 520;
  const h = 280;
  const spanX = x1 - x0 || 1;
  const spanY = hi - lo || 1;
  const px = (x: number) => padL + ((x - x0) / spanX) * (w - padL - padR);
  const py = (y: number) => padT + ((hi - y) / spanY) * (h - padT - padB);
  const path = nums
    .map((p, i) => `${i === 0 ? "M" : "L"}${px(xs[i]).toFixed(1)} ${py(p.n).toFixed(1)}`)
    .join(" ");
  const yTicks = 5;
  return (
    <svg className="inspect-graph" viewBox={`0 0 ${w} ${h}`} width="100%" height={h}>
      {Array.from({ length: yTicks + 1 }, (_, i) => {
        const v = lo + (spanY * i) / yTicks;
        const y = py(v);
        return (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={y} y2={y} stroke="#e2e8f0" />
            <text x={padL - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(v >= 100 ? 0 : 2)}
            </text>
          </g>
        );
      })}
      <line x1={padL} x2={padL} y1={padT} y2={h - padB} stroke="#94a3b8" />
      <line x1={padL} x2={w - padR} y1={h - padB} y2={h - padB} stroke="#94a3b8" />
      {nums.length > 1 ? <path d={path} fill="none" stroke="#1e293b" strokeWidth="1.4" /> : null}
      {nums.map((p, i) => (
        <circle key={p.key} cx={px(xs[i])} cy={py(p.n)} r="4" fill="#2563eb" />
      ))}
      {nums.map((p, i) => (
        <text key={`x${p.key}`} x={px(xs[i])} y={h - padB + 16} textAnchor="middle" fontSize="10" fill="#64748b">
          {useWl ? `${p.wavelength_nm}nm` : p.index}
        </text>
      ))}
    </svg>
  );
}

export default function InspectPage() {
  return (
    <ErrorBoundary title="影像查看出错">
      <InspectBody />
    </ErrorBoundary>
  );
}

function InspectBody() {
  const [meta, setMeta] = useState<InspectMeta | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [pixel, setPixel] = useState<InspectPixel | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"upload" | "parse" | null>(null);
  const [pct, setPct] = useState(0);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [colText, setColText] = useState("");
  const [rowText, setRowText] = useState("");
  const [coordText, setCoordText] = useState("");
  const [decimalsOn, setDecimalsOn] = useState(true);
  const [decimals, setDecimals] = useState(10);
  const [yMin, setYMin] = useState<number | null>(null);
  const [yMax, setYMax] = useState<number | null>(null);
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number>(0);
  const coordFocus = useRef(false);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  function syncInputs(next: InspectPixel, fromTyping = false) {
    setColText(String(next.col));
    setRowText(String(next.row));
    if (!fromTyping && !coordFocus.current) {
      if (next.x != null && next.y != null) {
        setCoordText(`${next.x}, ${next.y}`);
      } else {
        const xy = enviImageXY(next.col, next.row);
        setCoordText(`${xy.x}, ${xy.y}`);
      }
    }
  }

  async function queryPixel(current: InspectMeta, col: number, row: number, fromTyping = false) {
    const c = Math.min(current.width - 1, Math.max(0, Math.floor(col)));
    const r = Math.min(current.height - 1, Math.max(0, Math.floor(row)));
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const next = await api.inspectPixel(current.id, c, r, ac.signal);
      setPixel(next);
      syncInputs(next, fromTyping);
    } catch (err: any) {
      if (err?.name === "AbortError" || err?.name === "AuthExpiredError") return;
      message.error(err.message || String(err));
    }
  }

  async function loadFile(file: File) {
    setBusy(true);
    setPixel(null);
    setMeta(null);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
    setPhase("upload");
    setPct(0);
    try {
      const next = await api.inspectUpload(file, (nextPct, nextPhase) => {
        setPct(nextPct);
        setPhase(nextPhase);
      });
      const blob = await api.inspectPreview(next.id);
      const bands = Array.isArray(next.bands) ? next.bands : [];
      setImgSize({ w: 0, h: 0 });
      setPixel(null);
      setMeta({ ...next, bands });
      setPreview(URL.createObjectURL(blob));
    } catch (e: any) {
      if (e?.name === "AbortError" || e?.name === "AuthExpiredError") return;
      message.error(e.message || String(e));
    } finally {
      setBusy(false);
      setPhase(null);
      setPct(0);
    }
  }

  function pixelFromEvent(e: MouseEvent<HTMLImageElement>, current: InspectMeta) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const col = Math.min(current.width - 1, Math.max(0, Math.floor((x / rect.width) * current.width)));
    const row = Math.min(current.height - 1, Math.max(0, Math.floor((y / rect.height) * current.height)));
    return { x, y, col, row };
  }

  function onMove(e: MouseEvent<HTMLImageElement>) {
    if (!meta) return;
    const loc = pixelFromEvent(e, meta);
    setCursor({ x: loc.x, y: loc.y });
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      void queryPixel(meta, loc.col, loc.row);
    }, 20);
  }

  function onClick(e: MouseEvent<HTMLImageElement>) {
    if (!meta) return;
    const loc = pixelFromEvent(e, meta);
    setCursor({ x: loc.x, y: loc.y });
    void queryPixel(meta, loc.col, loc.row);
  }

  function submitCoord() {
    if (!meta) return;
    const parsed = parseCoordinate(coordText, meta);
    if (!parsed) {
      message.warning("坐标格式：列,行 或 列,-行，有地理信息时也可输入地面 X,Y");
      return;
    }
    void queryPixel(meta, parsed.col, parsed.row, true);
  }

  function submitRowCol() {
    if (!meta) return;
    const col = Number(colText);
    const row = Number(rowText);
    if (!Number.isFinite(col) || !Number.isFinite(row)) {
      message.warning("请输入列、行");
      return;
    }
    if (pixel && pixel.col === Math.floor(col) && pixel.row === Math.floor(row)) return;
    void queryPixel(meta, col, row, true);
  }

  const points = meta && pixel ? buildPoints(meta, pixel) : [];
  const footerValue = points.map((p) => formatDecimal(p.value, decimals, decimalsOn)).join(", ");
  const footerXY = pixel
    ? pixel.x != null && pixel.y != null
      ? `${pixel.x}, ${pixel.y}`
      : `${enviImageXY(pixel.col, pixel.row).x}, ${enviImageXY(pixel.col, pixel.row).y}`
    : "";

  const marker =
    meta && pixel && imgSize.w > 0
      ? {
          left: ((pixel.col + 0.5) / meta.width) * imgSize.w,
          top: ((pixel.row + 0.5) / meta.height) * imgSize.h,
        }
      : null;

  const xmp = meta?.xmp?.tags || [];
  const tiffTags = meta?.tiff?.tags || [];
  const exifRows = Object.entries(meta?.exif || {}).map(([k, v]) => ({ key: k, name: k, value: v }));

  return (
    <Card
      className="mosaic-panel inspect-page"
      title={
        <Space>
          <span>影像查看</span>
          <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
            上传 TIF / JPG，查看 XMP、波段与像元
          </Typography.Text>
        </Space>
      }
    >
      <div className="inspect-drop">
        <Upload.Dragger
          accept=".tif,.tiff,.jpg,.jpeg,image/tiff,image/jpeg"
          maxCount={1}
          showUploadList={false}
          disabled={busy}
          beforeUpload={(file) => {
            loadFile(file);
            return false;
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">将 TIF 或 JPG 拖到此处，或点击选择</p>
          <p className="ant-upload-hint">
            大图会抽样预览。鼠标点选或在下方输入坐标，按表格查看原始像元
          </p>
        </Upload.Dragger>
        {busy ? (
          <div className="inspect-drop-mask">
            <Spin />
            <Typography.Text>
              {phase === "parse" ? "正在解析影像…" : `正在上传 ${pct}%`}
            </Typography.Text>
            <Progress
              percent={phase === "parse" ? 100 : pct}
              status={phase === "parse" ? "active" : "normal"}
              style={{ width: 280 }}
            />
          </div>
        ) : null}
      </div>

      {!meta && !busy && (
        <Empty style={{ marginTop: 48 }} description="尚未打开文件" />
      )}

      {meta && (
        <>
          <div className="inspect-stage">
            <div className="inspect-viewer">
            {preview ? (
              <div className="inspect-viewer-stage">
                <img
                  src={preview}
                  alt={meta.filename}
                  onLoad={(e) =>
                    setImgSize({ w: e.currentTarget.clientWidth, h: e.currentTarget.clientHeight })
                  }
                  onMouseMove={onMove}
                  onClick={onClick}
                  onMouseLeave={() => setCursor(null)}
                  draggable={false}
                />
                {marker ? (
                  <span className="inspect-cross" style={{ left: marker.left, top: marker.top }} />
                ) : null}
                {cursor && pixel && (
                  <div
                    className="inspect-hud"
                    style={{ left: Math.min(cursor.x + 16, 280), top: Math.max(8, cursor.y - 8) }}
                  >
                    <div className="inspect-hud-swatch">
                      <span style={{ background: pixel.hex ? `#${pixel.hex}` : "#64748b" }} />
                    </div>
                    <div className="inspect-hud-grid">
                      <span>列,行</span>
                      <strong>
                        {pixel.col}, {pixel.row}
                      </strong>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <Empty description="预览生成中" />
            )}
          </div>

          <div className="inspect-probe">
            <div className="inspect-probe-toolbar">
              <div className="inspect-probe-row">
                <label>坐标</label>
                <Input
                  value={coordText}
                  placeholder="列,行  或  列,-行  或  地面X,Y"
                  onChange={(e) => setCoordText(e.target.value)}
                  onFocus={() => {
                    coordFocus.current = true;
                  }}
                  onBlur={() => {
                    coordFocus.current = false;
                  }}
                  onPressEnter={submitCoord}
                />
              </div>
              <div className="inspect-probe-row">
                <label>列</label>
                <Input
                  value={colText}
                  onChange={(e) => setColText(e.target.value)}
                  onPressEnter={submitRowCol}
                  style={{ width: 96 }}
                />
                <label>行</label>
                <Input
                  value={rowText}
                  onChange={(e) => setRowText(e.target.value)}
                  onPressEnter={submitRowCol}
                  style={{ width: 96 }}
                />
                <Checkbox checked={decimalsOn} onChange={(e) => setDecimalsOn(e.target.checked)}>
                  小数
                </Checkbox>
                <InputNumber
                  min={0}
                  max={12}
                  value={decimals}
                  disabled={!decimalsOn}
                  onChange={(v) => setDecimals(Number(v ?? 10))}
                  style={{ width: 72 }}
                />
              </div>
            </div>
            <Tabs
              size="small"
              destroyInactiveTabPane
              items={[
                {
                  key: "table",
                  label: "表格",
                  children: (
                    <Table
                      size="small"
                      pagination={false}
                      rowKey="key"
                      dataSource={points}
                      locale={{ emptyText: "在图上点选或输入坐标" }}
                      columns={[
                        { title: "图层", dataIndex: "layer", ellipsis: true },
                        {
                          title: "值",
                          dataIndex: "value",
                          width: 168,
                          render: (v) => formatDecimal(v, decimals, decimalsOn),
                        },
                        {
                          title: "行",
                          dataIndex: "row",
                          width: 72,
                        },
                        {
                          title: "列",
                          dataIndex: "col",
                          width: 72,
                        },
                      ]}
                    />
                  ),
                },
                {
                  key: "graph",
                  label: "曲线",
                  children: (
                    <div>
                      <Space style={{ marginBottom: 8 }}>
                        <span>Y min</span>
                        <InputNumber
                          value={yMin ?? undefined}
                          onChange={(v) => setYMin(typeof v === "number" ? v : null)}
                          placeholder="自动"
                        />
                        <span>Y max</span>
                        <InputNumber
                          value={yMax ?? undefined}
                          onChange={(v) => setYMax(typeof v === "number" ? v : null)}
                          placeholder="自动"
                        />
                      </Space>
                      <ProbeGraph points={points} yMin={yMin} yMax={yMax} />
                    </div>
                  ),
                },
              ]}
            />
            <div className="inspect-probe-foot">
              {pixel
                ? `Coordinate:'${footerXY}${footerValue ? `,${footerValue}` : ""}'`
                : "Coordinate:"}
            </div>
          </div>
          </div>

          {meta.stats_sampled ? (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 12 }}
              message="大图已抽样预览"
              description="波段统计来自预览网格。表格里的像元值仍读文件原始数据。"
            />
          ) : null}

          <div className="inspect-meta">
            <Tabs
              size="small"
              items={[
                {
                  key: "file",
                  label: "文件",
                  children: (
                    <Descriptions size="small" column={2} bordered>
                      <Descriptions.Item label="文件名">{meta.filename}</Descriptions.Item>
                      <Descriptions.Item label="格式">{(meta.format || "").toUpperCase() || "—"}</Descriptions.Item>
                      <Descriptions.Item label="波段">
                        {meta.bands.map((b) => b.name).join(" / ")}
                      </Descriptions.Item>
                      <Descriptions.Item label="数据类型">{meta.dtype}</Descriptions.Item>
                      <Descriptions.Item label="大小">{bytes(meta.size_bytes)}</Descriptions.Item>
                      <Descriptions.Item label="NoData">{fmt(meta.nodata)}</Descriptions.Item>
                      {meta.tiff?.compression ? (
                        <Descriptions.Item label="压缩">{String(meta.tiff.compression)}</Descriptions.Item>
                      ) : null}
                      {meta.geotransform ? (
                        <Descriptions.Item label="原点">
                          {fmt(meta.geotransform.origin_x)}, {fmt(meta.geotransform.origin_y)}
                        </Descriptions.Item>
                      ) : null}
                    </Descriptions>
                  ),
                },
                {
                  key: "xmp",
                  label: `XMP (${xmp.length})`,
                  children: (
                    <>
                      <Table
                        size="small"
                        rowKey={(r) => `${r.ns}:${r.name}:${r.value}`}
                        pagination={false}
                        dataSource={xmp}
                        locale={{ emptyText: "没有 XMP" }}
                        columns={[
                          { title: "命名空间", dataIndex: "ns", width: 110, ellipsis: true },
                          { title: "字段", dataIndex: "name", width: 180, ellipsis: true },
                          { title: "值", dataIndex: "value", ellipsis: true },
                        ]}
                      />
                      <Collapse
                        style={{ marginTop: 12 }}
                        items={[
                          {
                            key: "raw",
                            label: "原始 XMP",
                            children: <pre className="inspect-raw">{meta.xmp?.raw || "（无）"}</pre>,
                          },
                        ]}
                      />
                    </>
                  ),
                },
                {
                  key: "bands",
                  label: `波段 (${meta.bands.length})`,
                  children: (
                    <Table
                      size="small"
                      rowKey="index"
                      pagination={false}
                      dataSource={meta.bands}
                      columns={[
                        { title: "#", dataIndex: "index", width: 48 },
                        { title: "波段", dataIndex: "name", width: 90 },
                        {
                          title: "波长",
                          dataIndex: "wavelength_nm",
                          width: 80,
                          render: (v: number | null) => (v == null ? "—" : `${Number(v)}nm`),
                        },
                        { title: "最小", dataIndex: "min", render: fmt },
                        { title: "中位", dataIndex: "p50", render: fmt },
                        { title: "最大", dataIndex: "max", render: fmt },
                      ]}
                    />
                  ),
                },
                {
                  key: "tags",
                  label: "标签",
                  children: (
                    <>
                      {exifRows.length > 0 && (
                        <Table
                          size="small"
                          title={() => "EXIF"}
                          rowKey="key"
                          pagination={false}
                          dataSource={exifRows}
                          columns={[
                            { title: "字段", dataIndex: "name", width: 160, ellipsis: true },
                            { title: "值", dataIndex: "value", render: fmt, ellipsis: true },
                          ]}
                        />
                      )}
                      {tiffTags.length > 0 && (
                        <Table
                          size="small"
                          title={() => "TIFF 标签"}
                          rowKey={(r) => `${r.id}-${r.name}`}
                          pagination={{ pageSize: 20 }}
                          dataSource={tiffTags}
                          columns={[
                            { title: "ID", dataIndex: "id", width: 70 },
                            { title: "名称", dataIndex: "name", width: 180, ellipsis: true },
                            { title: "值", dataIndex: "value", render: fmt, ellipsis: true },
                          ]}
                        />
                      )}
                      {!exifRows.length && !tiffTags.length && <Empty description="没有 EXIF / TIFF 标签" />}
                    </>
                  ),
                },
              ]}
            />
          </div>
        </>
      )}
    </Card>
  );
}
