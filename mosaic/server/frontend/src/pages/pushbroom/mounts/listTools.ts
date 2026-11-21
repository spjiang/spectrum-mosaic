import type { TablePaginationConfig } from "antd";

/** 安装配置各表共用分页（数据来自数据库，前端对当前页列表筛选） */
export function mountsTablePagination(total: number): TablePaginationConfig {
  return {
    defaultPageSize: 10,
    showSizeChanger: true,
    pageSizeOptions: [10, 20, 50],
    showTotal: (n) => `共 ${n} 条`,
    hideOnSinglePage: false,
  };
}

export function matchMountKeyword(name: string, keyword: string): boolean {
  const q = keyword.trim().toLowerCase();
  if (!q) return true;
  return name.toLowerCase().includes(q);
}