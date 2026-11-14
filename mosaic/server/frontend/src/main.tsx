import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import { AuthProvider } from "./auth";
import { RbacProvider } from "./rbac";
import { mosaicTheme } from "./theme";
import "antd/dist/reset.css";
import "./theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={mosaicTheme}>
    <AuthProvider>
      <RbacProvider>
        <App />
      </RbacProvider>
    </AuthProvider>
  </ConfigProvider>
);
