import { useEffect, useRef, useState } from "react";
import { Tooltip, message } from "antd";
import { api } from "../api";
import { ImageInspectModal } from "./ImageStage";

type Props = {
  jobId: string;
  hasRgb: boolean;
  title?: string;
  rev?: string | null;
};

const MAX_PARALLEL = 2;
let active = 0;
const waiters: Array<() => void> = [];

function runLimited<T>(task: () => Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const start = () => {
      active += 1;
      task()
        .then(resolve, reject)
        .finally(() => {
          active -= 1;
          const next = waiters.shift();
          if (next) next();
        });
    };
    if (active < MAX_PARALLEL) start();
    else waiters.push(start);
  });
}

export default function JobRgbThumbnail({ jobId, hasRgb, title, rev }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [full, setFull] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loadingHd, setLoadingHd] = useState(false);
  const [failed, setFailed] = useState(false);
  const request = useRef(0);

  useEffect(() => {
    if (!hasRgb) {
      setUrl(null);
      setFull(null);
      setFailed(false);
      setOpen(false);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    request.current += 1;
    setFailed(false);
    setFull(null);
    setOpen(false);
    runLimited(() => api.rgbThumb(jobId, rev))
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      request.current += 1;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, hasRgb, rev]);

  useEffect(() => {
    return () => {
      if (full) URL.revokeObjectURL(full);
    };
  }, [full]);

  function openLarge() {
    if (full || loadingHd) {
      setOpen(true);
      return;
    }
    const token = request.current + 1;
    request.current = token;
    setLoadingHd(true);
    setOpen(true);
    api
      .rgbHd(jobId, rev)
      .then((blob) => {
        if (request.current !== token) return;
        setFull(URL.createObjectURL(blob));
      })
      .catch((error: Error) => {
        if (request.current !== token) return;
        message.error(error.message || "高清图读取失败");
        setOpen(false);
      })
      .finally(() => {
        if (request.current === token) setLoadingHd(false);
      });
  }

  if (!hasRgb) {
    return (
      <Tooltip title="正射 Color 尚未写出">
        <span className="job-rgb-thumb job-rgb-thumb--empty" aria-hidden />
      </Tooltip>
    );
  }

  if (failed || !url) {
    return (
      <Tooltip title={failed ? "预览加载失败" : "加载预览…"}>
        <span className={`job-rgb-thumb job-rgb-thumb--${failed ? "err" : "loading"}`} aria-hidden />
      </Tooltip>
    );
  }

  const tip = title ? `${title} · 点击放大` : "点击放大";
  const viewTitle = title ? `${title} RGB` : "RGB 正射";
  return (
    <>
      <Tooltip title={tip} open={open ? false : undefined}>
        <span className="job-rgb-thumb-wrap" onClick={(e) => e.stopPropagation()}>
          <button type="button" className="job-rgb-thumb-btn" aria-label={tip} onClick={openLarge}>
            <img src={url} alt={viewTitle} className="job-rgb-thumb" />
            <span className="job-rgb-thumb-mask">{loadingHd ? "读取高清…" : "放大"}</span>
          </button>
        </span>
      </Tooltip>
      <ImageInspectModal
        open={open}
        src={full}
        title={viewTitle}
        emptyText={loadingHd ? "正在读取高清…" : "正在打开影像…"}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
