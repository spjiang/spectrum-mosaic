import type { TablePaginationConfig } from "antd";

export const LIST_PAGE: TablePaginationConfig = {
  pageSize: 10,
  hideOnSinglePage: true,
  showSizeChanger: true,
  showTotal: (n) => `共 ${n} 条`,
};
