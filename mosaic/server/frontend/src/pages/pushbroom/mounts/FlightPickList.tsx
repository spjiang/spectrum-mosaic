import { useMemo, useState } from "react";
import { Checkbox, Flex, Input, Pagination, Typography } from "antd";
import type { PushbroomFlightItem } from "../../../api";
import { flightDay, flightSize, formatBytes } from "../labels";
import { matchMountKeyword } from "./listTools";

/** 标定选架次：网格列宽铺满，支持搜索与分页 */
export default function FlightPickList({
  flights,
  picked,
  max,
  onChange,
}: {
  flights: PushbroomFlightItem[];
  picked: string[];
  max: number;
  onChange: (next: string[]) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const filtered = useMemo(
    () => flights.filter((f) => matchMountKeyword(f.name, keyword)),
    [flights, keyword],
  );

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  const keys = pageRows.map((f) => f.input_dir);
  const checkedOnPage = keys.filter((k) => picked.includes(k)).length;
  const allChecked = keys.length > 0 && checkedOnPage === keys.length;
  const indeterminate = checkedOnPage > 0 && !allChecked;

  function toggleAll(checked: boolean) {
    if (checked) {
      const merge = new Set([...picked, ...keys]);
      const next = [...merge];
      if (next.length > max) onChange(next.slice(0, max));
      else onChange(next);
    } else {
      onChange(picked.filter((d) => !keys.includes(d)));
    }
  }

  function toggleOne(dir: string, checked: boolean) {
    if (checked) {
      if (picked.length >= max) return;
      onChange([...picked, dir]);
    } else {
      onChange(picked.filter((d) => d !== dir));
    }
  }

  return (
    <Flex vertical gap={8} style={{ width: "100%" }}>
      <Flex className="pb-mounts-toolbar" gap={8} wrap align="center">
        <Input
          allowClear
          placeholder="搜索架次名"
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            setPage(1);
          }}
          style={{ width: 220 }}
        />
        <Typography.Text type="secondary">共 {filtered.length} 个可用架次</Typography.Text>
      </Flex>
      <div className="pb-flight-pick">
        <div className="pb-flight-pick-row pb-flight-pick-head">
          <Checkbox
            checked={allChecked}
            indeterminate={indeterminate}
            disabled={pageRows.length === 0}
            onChange={(e) => toggleAll(e.target.checked)}
          />
          <span>架次</span>
          <span>飞行日期</span>
          <span>数据大小</span>
          <span>影像尺寸</span>
        </div>
        {pageRows.length === 0 ? (
          <div className="pb-flight-pick-empty">没有符合筛选的架次</div>
        ) : (
          pageRows.map((f) => {
            const checked = picked.includes(f.input_dir);
            const blocked = !checked && picked.length >= max;
            return (
              <label
                key={f.input_dir}
                className={`pb-flight-pick-row${checked ? " is-checked" : ""}${blocked ? " is-disabled" : ""}`}
              >
                <Checkbox
                  checked={checked}
                  disabled={blocked}
                  onChange={(e) => toggleOne(f.input_dir, e.target.checked)}
                />
                <Typography.Text ellipsis={{ tooltip: f.name }}>{f.name}</Typography.Text>
                <span>{flightDay(f.flight) || "—"}</span>
                <span>{formatBytes(f.flight?.spe_bytes)}</span>
                <Typography.Text type="secondary" ellipsis={{ tooltip: flightSize(f.flight) }}>
                  {flightSize(f.flight)}
                </Typography.Text>
              </label>
            );
          })
        )}
      </div>
      <div className="pb-mounts-pick-pager">
        <Pagination
          size="small"
          current={page}
          pageSize={pageSize}
          total={filtered.length}
          showSizeChanger
          pageSizeOptions={[5, 10, 20]}
          showTotal={(t) => `共 ${t} 条`}
          onChange={(p, size) => {
            setPage(p);
            setPageSize(size);
          }}
        />
      </div>
    </Flex>
  );
}