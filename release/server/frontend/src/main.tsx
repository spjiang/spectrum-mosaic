import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { mosaicTheme } from "./theme";
import "antd/dist/reset.css";
import "./theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <ConfigProvider locale={zhCN} theme={mosaicTheme}>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </ConfigProvider>
);
