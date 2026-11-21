import { useEffect, useRef, useState } from "react";
import { api, type AccuracyView } from "../api";
import ImageStage from "./ImageStage";

/** 整幅正射一次载入，拖动和缩放只改画面，不再向服务器要窗口。 */
export default function OrthoStage({ jobId, band }: { jobId: string; band: string }) {
  const [image, setImage] = useState<AccuracyView | null>(null);
  const [note, setNote] = useState("");
  const overviewUrl = useRef("");
  const sharpUrl = useRef("");

  useEffect(() => {
    return () => {
      if (overviewUrl.current) URL.revokeObjectURL(overviewUrl.current);
      if (sharpUrl.current) URL.revokeObjectURL(sharpUrl.current);
    };
  }, []);

  useEffect(() => {
    if (!jobId || !band) return;
    let cancel = false;
    setImage(null);
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
        show(next, true);
      })
      .catch(() => {
        /* 清晰图未就绪时继续用已载入的整幅图 */
      });
    return () => {
      cancel = true;
    };
  }, [jobId, band]);

  return (
    <ImageStage
      image={image}
      note={note}
      resetKey={`${jobId}:${band}`}
      alt="拼图影像"
      overviewAlt="整幅正射"
      emptyText="正在打开整幅拼图…"
    />
  );
}
