import { theme } from "antd";
import type { ThemeConfig } from "antd";

/** 浅色企业风：钢蓝主色，白底工作区 */
export const mosaicTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: "#1B6BBE",
    colorSuccess: "#1F9D63",
    colorWarning: "#D48806",
    colorError: "#D4380D",
    colorInfo: "#1B6BBE",
    colorBgBase: "#F4F7FB",
    colorBgContainer: "#FFFFFF",
    colorBgElevated: "#FFFFFF",
    colorBorder: "#E2E8F0",
    colorBorderSecondary: "#EEF2F7",
    colorText: "#1E293B",
    colorTextSecondary: "#64748B",
    borderRadius: 8,
    fontFamily:
      '"IBM Plex Sans", "Source Han Sans SC", "Noto Sans SC", "PingFang SC", sans-serif',
  },
  components: {
    Layout: {
      siderBg: "#FFFFFF",
      headerBg: "#FFFFFF",
      bodyBg: "#F4F7FB",
      triggerBg: "#F4F7FB",
    },
    Menu: {
      itemBg: "transparent",
      subMenuItemBg: "transparent",
      itemSelectedBg: "rgba(27, 107, 190, 0.1)",
      itemSelectedColor: "#1B6BBE",
      itemHoverBg: "rgba(27, 107, 190, 0.06)",
    },
    Card: {
      colorBgContainer: "#FFFFFF",
    },
    Table: {
      headerBg: "#F8FAFC",
      rowHoverBg: "rgba(27, 107, 190, 0.04)",
    },
  },
};
