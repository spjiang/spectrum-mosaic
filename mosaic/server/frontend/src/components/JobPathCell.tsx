import { CopyOutlined } from "@ant-design/icons";
import { Button, Tooltip, message } from "antd";

export default function JobPathCell({ value }: { value?: string | null }) {
  if (!value) return <>—</>;
  return (
    <div className="job-path-cell-inner">
      <Tooltip title={value}>
        <span className="job-path-text">{value}</span>
      </Tooltip>
      <Tooltip title="复制路径">
        <Button
          type="text"
          size="small"
          className="job-path-copy"
          icon={<CopyOutlined />}
          aria-label="复制路径"
          onClick={(e) => {
            e.stopPropagation();
            void navigator.clipboard.writeText(value).then(
              () => message.success("已复制"),
              () => message.error("复制失败"),
            );
          }}
        />
      </Tooltip>
    </div>
  );
}
