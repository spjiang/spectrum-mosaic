import { Popover } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { PARAM_BRIEF } from "../paramBrief";
import { PARAM_HELP } from "../paramHelp";

export function ParamExplainBody({
  paramKey,
  description,
  rich = false,
}: {
  paramKey: string;
  description?: string;
  rich?: boolean;
}) {
  const brief = PARAM_BRIEF[paramKey];
  const extra = PARAM_HELP[paramKey];
  return (
    <div className="param-explain">
      <section>
        <div className="param-explain-title">作用</div>
        <p>{brief?.role || description || "控制这次拼图任务的一项输入。"}</p>
      </section>
      <section>
        <div className="param-explain-title">如何配置</div>
        <p>{brief?.how || "在处理方案里按默认值填写。留空表示自动或不启用。"}</p>
      </section>
      <section>
        <div className="param-explain-title">对拼图的效果</div>
        <p>{brief?.effect || extra?.up || extra?.off || "改变这项会改变成果的范围、速度或数值，详见处理方案中的完整说明。"}</p>
      </section>
      {rich && description ? (
        <section>
          <div className="param-explain-title">说明</div>
          <p>{description}</p>
        </section>
      ) : null}
      {rich && extra?.examples?.length ? (
        <section>
          <div className="param-explain-title">填写示例</div>
          {extra.examples.map((ex) => (
            <p key={ex} className="param-explain-example">
              {ex}
            </p>
          ))}
        </section>
      ) : null}
      {rich && extra?.tips?.length ? (
        <section>
          <div className="param-explain-title">注意</div>
          <ul>
            {extra.tips.map((tip) => (
              <li key={tip}>{tip}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {rich && (extra?.up || extra?.down || extra?.off) ? (
        <section>
          <div className="param-explain-title">调参效果</div>
          <ul>
            {extra.up ? <li>调大 / 打开：{extra.up}</li> : null}
            {extra.down ? <li>调小：{extra.down}</li> : null}
            {extra.off ? <li>关闭 / 留空：{extra.off}</li> : null}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

export default function ParamHelpMark({
  paramKey,
  label,
  description,
  rich = false,
}: {
  paramKey: string;
  label: string;
  description?: string;
  rich?: boolean;
}) {
  return (
    <Popover
      trigger="click"
      placement="rightTop"
      zIndex={1200}
      overlayClassName="param-explain-popover"
      title={`${label}`}
      content={<ParamExplainBody paramKey={paramKey} description={description} rich={rich} />}
    >
      <button type="button" className="param-help-btn" aria-label={`${label}说明`} onClick={(event) => event.stopPropagation()}>
        <QuestionCircleOutlined />
      </button>
    </Popover>
  );
}
