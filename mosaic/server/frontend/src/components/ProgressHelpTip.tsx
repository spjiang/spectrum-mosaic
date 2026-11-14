import { Popover } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { STAGE_META, STAGES, type StageId } from "../stages";

function fmtPct(n: number) {
  if (n === 0 || n === 100) return `${n}%`;
  return `≈${n}%`;
}

export function ProgressHelpContent() {
  const n = STAGES.length;
  return (
    <div className="progress-help">
      <p>总进度按 {n} 个阶段等权，不是按实际耗时：</p>
      <p className="progress-help-formula">（阶段序号 + 本阶段完成比例）/ {n} × 100</p>
      <table>
        <thead>
          <tr>
            <th>阶段</th>
            <th>序号</th>
            <th>开始</th>
            <th>结束</th>
          </tr>
        </thead>
        <tbody>
          {STAGES.map((id, i) => (
            <tr key={id}>
              <td>{STAGE_META[id as StageId].label}</td>
              <td>{i}</td>
              <td>{fmtPct(Math.round((i / n) * 100))}</td>
              <td>{fmtPct(Math.round(((i + 1) / n) * 100))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>阶段内部（空三匹配、平差等）通常不往上加。空三会长时间停在约 29%，完成后跳到约 43%。</p>
    </div>
  );
}

export default function ProgressHelpTip() {
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      title="进度说明"
      overlayClassName="progress-help-popover"
      destroyTooltipOnHide
      getPopupContainer={() => document.body}
      content={<ProgressHelpContent />}
    >
      <button
        type="button"
        className="param-help-btn"
        aria-label="进度说明"
        onClick={(e) => e.stopPropagation()}
      >
        <QuestionCircleOutlined />
      </button>
    </Popover>
  );
}

export function TitleWithProgressHelp({ children }: { children: React.ReactNode }) {
  return (
    <span className="title-with-help">
      {children}
      <ProgressHelpTip />
    </span>
  );
}
