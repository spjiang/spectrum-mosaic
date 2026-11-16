import type { TablePaginationConfig } from "antd";

export const LIST_PAGE: TablePaginationConfig = {
  pageSize: 10,
  hideOnSinglePage: false,
  showSizeChanger: true,
  showQuickJumper: true,
  showTotal: (n) => `共 ${n} 条`,
};
