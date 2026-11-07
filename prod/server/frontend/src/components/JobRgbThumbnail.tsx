import { useEffect, useState } from "react";
import { Image, Tooltip, message } from "antd";
import { api } from "../api";

type Props = {
  jobId: string;
  hasRgb: boolean;
  title?: string;
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

export default function JobRgbThumbnail({ jobId, hasRgb, title }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [full, setFull] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [loadingHd, setLoadingHd] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hasRgb) {
      setUrl(null);
      setFull(null);
      setFailed(false);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    setFailed(false);
    setFull(null);
    setOpen(false);
    runLimited(() => api.rgbThumb(jobId))
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
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, hasRgb]);

  function openLarge(visible: boolean) {
    if (!visible) {
      setOpen(false);
      return;
    }
    if (full) {
      setOpen(true);
      return;
    }
    if (loadingHd) return;
    setLoadingHd(true);
    api
      .rgbHd(jobId)
      .then((blob) => {
        setFull(URL.createObjectURL(blob));
        setOpen(true);
      })
      .catch((error: Error) => message.error(error.message || "高清图读取失败"))
      .finally(() => setLoadingHd(false));
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
  return (
    <Tooltip title={tip}>
      <span className="job-rgb-thumb-wrap" onClick={(e) => e.stopPropagation()}>
        <Image
          src={url}
          alt={title || "RGB 正射"}
          className="job-rgb-thumb"
          preview={{
            visible: open,
            src: full || url,
            mask: loadingHd ? "读取高清…" : "放大",
            onVisibleChange: openLarge,
          }}
        />
      </span>
    </Tooltip>
  );
}
