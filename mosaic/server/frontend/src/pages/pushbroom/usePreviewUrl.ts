import { useEffect, useState } from "react";
import { api } from "../../api";

/** 结果预览要带登录头，取成 blob 地址。换任务或卸载时回收。 */
export function usePreviewUrl(jobId: string | null | undefined, rev?: string | null) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!jobId) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    api
      .pushbroomPreview(jobId, rev)
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
  }, [jobId, rev]);

  return { url, failed };
}