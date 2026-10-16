import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  QuestionCircleOutlined,
  SaveOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { PARAM_HELP } from "../paramHelp";
import { formatDefault, isEmpty, isRequired, paramRemark, validateValues, type ParamDef } from "../paramRules";
import { PRESET_OPTIONS, RUN_MODE_OPTIONS } from "../presets";
import { STAGE_META, STAGES, type StageId } from "../stages";
import { downloadJson, profileFilename, serializeProfile } from "../profileExport";
import { useAuth } from "../auth";
import { canConfigure } from "../roles";

function FieldEditor({
  d,
  values,
  setValues,
  onHelp,
  error,
}: {
  d: ParamDef;
  values: Record<string, any>;
  setValues: (v: Record<string, any>) => void;
  onHelp: (d: ParamDef) => void;
  error?: string;
}) {
  const required = isRequired(d, values);
  const hasDefault = d.default_value !== null && d.default_value !== undefined;
  return (
    <div className={`param-field${required ? " is-required" : ""}${error ? " is-error" : ""}`}>
      <div className="param-field-head">
        <Space size={6} wrap>
          <Typography.Text strong>{d.key}</Typography.Text>
          {paramRemark(d) ? <span className="param-field-zh">{paramRemark(d)}</span> : null}
          {required ? <Tag color="red">必填</Tag> : <Tag>选填</Tag>}
          {d.advanced ? <Tag>高级</Tag> : null}
        </Space>
        <button
          type="button"
          className="param-help-btn"
          title="参数说明"
          onClick={(e) => {
            e.stopPropagation();
            onHelp(d);
          }}
        >
          <QuestionCircleOutlined />
        </button>
      </div>
      {d.value_type === "bool" ? (
        <Switch checked={!!values[d.key]} onChange={(c) => setValues({ ...values, [d.key]: c })} />
      ) : d.key === "run_mode" ? (
        <Select
          style={{ width: "100%" }}
          value={values[d.key] || "full"}
          options={[...RUN_MODE_OPTIONS]}
          onChange={(v) => setValues({ ...values, [d.key]: v })}
        />
      ) : d.key === "preset" ? (
        <Select
          allowClear
          style={{ width: "100%" }}
          value={values[d.key] || ""}
          options={[...PRESET_OPTIONS]}
          onChange={(v) => setValues({ ...values, [d.key]: v || null })}
        />
      ) : d.key === "start_stage" || d.key === "stop_after_stage" ? (
        <Select
          allowClear={d.key === "stop_after_stage"}
          style={{ width: "100%" }}
          value={values[d.key] || undefined}
          placeholder={required ? "请选择" : "留空"}
          options={STAGES.map((s) => ({ value: s, label: `${STAGE_META[s].label}（${s}）` }))}
          onChange={(v) => setValues({ ...values, [d.key]: v || null })}
        />
      ) : d.key === "bands" ? (
        <Select
          mode="tags"
          style={{ width: "100%" }}
          value={values[d.key] || []}
          onChange={(v) => setValues({ ...values, [d.key]: v })}
        />
      ) : (
        <Input
          status={error ? "error" : undefined}
          value={values[d.key] ?? ""}
          placeholder={required ? "必填" : hasDefault ? `默认 ${formatDefault(d)}` : "选填，空则自动"}
          onChange={(e) => {
            const raw = e.target.value;
            let parsed: any = raw;
            if (d.value_type === "int" || d.value_type === "float") {
              parsed = raw === "" ? null : Number(raw);
            }
            setValues({ ...values, [d.key]: parsed });
          }}
        />
      )}
      <div className="param-field-meta">
        {hasDefault ? `默认：${formatDefault(d)}` : "默认：空（自动 / 不启用）"}
        {required && d.required_when ? " · 当前运行模式下必填" : ""}
      </div>
      {error ? (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          {error}
        </Typography.Text>
      ) : null}
    </div>
  );
}

function fieldSpan(d: ParamDef): number {
  if (d.value_type === "path" || d.key === "bands" || d.key === "input_dir" || d.key === "output_dir") {
    return 24;
  }
  return 12;
}

export default function ProfileEditPage() {
  const { id } = useParams();
  const isNew = !id;
  const nav = useNavigate();
  const { roles } = useAuth();
  const allowEdit = canConfigure(roles);
  const [defs, setDefs] = useState<ParamDef[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [activeStage, setActiveStage] = useState<StageId | null>(null);
  const [helpDef, setHelpDef] = useState<ParamDef | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const grouped = useMemo(() => {
    const g: Record<string, ParamDef[]> = {};
    for (const d of defs) {
      (g[d.stage_id] ||= []).push(d);
    }
    return g;
  }, [defs]);

  const allErrors = useMemo(() => validateValues(defs, values), [defs, values]);
  const errorMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const e of allErrors) m[e.key] = e.message;
    return { ...m, ...errors };
  }, [allErrors, errors]);

  const stageStats = (stage: string) => {
    const items = grouped[stage] || [];
    const req = items.filter((d) => isRequired(d, values));
    const reqFilled = req.filter((d) => !isEmpty(values[d.key])).length;
    const optional = items.length - req.length;
    const optFilled = items.filter((d) => !isRequired(d, values) && !isEmpty(values[d.key])).length;
    return { req: req.length, reqFilled, optional, optFilled, total: items.length };
  };

  useEffect(() => {
    (async () => {
      try {
        const [d, profiles] = await Promise.all([api.paramDefs(), api.profiles()]);
        setDefs(d);
        const init: Record<string, any> = {};
        for (const x of d) init[x.key] = x.default_value;

        if (isNew) {
          setName("未命名模版");
          setDescription("");
          setValues(init);
          return;
        }

        const p = profiles.find((x: any) => String(x.id) === String(id));
        if (!p) {
          message.error("模版不存在");
          nav("/profiles");
          return;
        }
        setName(p.name);
        setDescription(p.description || "");
        setValues({ ...init, ...(p.values || {}) });
      } catch (e: any) {
        message.error(String(e.message || e));
      }
    })();
  }, [id, isNew, nav]);

  async function save() {
    if (!name.trim()) {
      message.warning("请填写模版名称");
      return;
    }
    const errs = validateValues(defs, values);
    if (errs.length) {
      const map: Record<string, string> = {};
      for (const e of errs) map[e.key] = e.message;
      setErrors(map);
      const first = defs.find((d) => map[d.key]);
      if (first) setActiveStage(first.stage_id as StageId);
      message.warning(errs[0].message);
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      if (isNew) {
        await api.createProfile({
          name: name.trim(),
          description,
          values,
          preset: values.preset || null,
        });
        message.success("已创建模版");
      } else {
        await api.updateProfile(Number(id), {
          name: name.trim(),
          description,
          values,
          preset: values.preset || null,
        });
        message.success("已保存方案");
      }
      nav("/profiles");
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setSaving(false);
    }
  }

  const stageDefs = activeStage ? grouped[activeStage] || [] : [];
  const helpExtra = helpDef ? PARAM_HELP[helpDef.key] : undefined;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => nav("/profiles")} style={{ paddingLeft: 0 }}>
        返回处理方案
      </Button>

      <Card
        className="mosaic-panel"
        title={isNew ? "新建处理方案" : allowEdit ? `编辑方案 #${id}` : `查看方案 #${id}`}
        extra={
          <Space>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                if (!name.trim()) {
                  message.warning("请先填写方案名称");
                  return;
                }
                downloadJson(
                  profileFilename({ id: isNew ? undefined : Number(id), name }),
                  serializeProfile({
                    id: isNew ? undefined : Number(id),
                    name,
                    description,
                    preset: values.preset || null,
                    values,
                    version: undefined,
                  }),
                );
                message.success("已下载当前参数");
              }}
            >
              下载参数
            </Button>
            {allowEdit && (
              <>
            <Button
              onClick={() => {
                setValues((v) => ({
                  ...v,
                  preset: "rgb_preview",
                  bands: ["Color"],
                  run_mode: "full",
                }));
                message.success("已应用 RGB 快速预览预设");
              }}
            >
              RGB 预览预设
            </Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>
              保存方案
            </Button>
              </>
            )}
          </Space>
        }
      >
        <Form layout="vertical" style={{ maxWidth: 720 }}>
          <Form.Item label="方案名称" required>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：本测区 · 全流程" />
            {!isNew && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                以 ID #{id} 标识，名称可随时修改（不可与其它方案重名）。
              </Typography.Text>
            )}
          </Form.Item>
          <Form.Item label="说明">
            <Input.TextArea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="用途说明，便于现场选用"
            />
          </Form.Item>
        </Form>
      </Card>

      <Card
        className="mosaic-panel"
        title="拼图执行工作流"
        extra={<Typography.Text type="secondary">红色为必填未填；点击阶段打开参数</Typography.Text>}
      >
        <div className="workflow-rail">
          {STAGES.map((stage, idx) => {
            const meta = STAGE_META[stage];
            const st = stageStats(stage);
            const reqOk = st.req === 0 || st.reqFilled === st.req;
            return (
              <div key={stage} className="workflow-item">
                {idx > 0 && <div className="workflow-arrow" aria-hidden />}
                <button
                  type="button"
                  className={`workflow-node${reqOk ? "" : " is-warn"}`}
                  onClick={() => setActiveStage(stage)}
                >
                  <div className="workflow-node-index">{idx}</div>
                  <div className="workflow-node-body">
                    <div className="workflow-node-title">{meta.label}</div>
                    <div className="workflow-node-code">{stage}</div>
                    <div className="workflow-node-hint">{meta.hint}</div>
                    <div className="workflow-node-meta">
                      {st.req > 0 ? (
                        <Tag color={reqOk ? "blue" : "red"} style={{ margin: 0 }}>
                          必填 {st.reqFilled}/{st.req}
                        </Tag>
                      ) : (
                        <Tag style={{ margin: 0 }}>
                          选填 {st.optFilled}/{st.optional}
                        </Tag>
                      )}
                      <SettingOutlined style={{ color: "var(--mosaic-primary)" }} />
                    </div>
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      </Card>

      <Modal
        title={activeStage ? `${STAGE_META[activeStage].label}（${activeStage}）` : "阶段参数"}
        open={!!activeStage}
        onCancel={() => setActiveStage(null)}
        onOk={() => {
          const stageErrs = stageDefs.filter((d) => errorMap[d.key]);
          if (stageErrs.length) {
            message.warning(errorMap[stageErrs[0].key]);
            return;
          }
          setActiveStage(null);
        }}
        okText="完成"
        cancelText="关闭"
        width={1080}
        styles={{ body: { maxHeight: "70vh", overflowY: "auto", paddingTop: 8 } }}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
          {activeStage ? STAGE_META[activeStage].hint : ""}。必填项需填写；选填留空则用默认值或自动估计。
        </Typography.Paragraph>
        {stageDefs.length === 0 ? (
          <Typography.Text type="secondary">该阶段暂无参数项</Typography.Text>
        ) : (
          <Row gutter={[20, 8]}>
            {stageDefs.map((d) => (
              <Col key={d.key} xs={24} md={fieldSpan(d)}>
                <FieldEditor
                  d={d}
                  values={values}
                  setValues={setValues}
                  onHelp={setHelpDef}
                  error={errorMap[d.key]}
                />
              </Col>
            ))}
          </Row>
        )}
      </Modal>

      <Modal
        title={
          helpDef ? `参数说明 · ${helpDef.key}${paramRemark(helpDef) ? `（${paramRemark(helpDef)}）` : ""}` : "参数说明"
        }
        open={!!helpDef}
        onCancel={() => setHelpDef(null)}
        onOk={() => setHelpDef(null)}
        okText="知道了"
        cancelButtonProps={{ style: { display: "none" } }}
        width={640}
        zIndex={1100}
        destroyOnClose
      >
        {helpDef && (
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <div>
              <Typography.Text type="secondary">标记</Typography.Text>
              <div>
                {isRequired(helpDef, values) ? <Tag color="red">必填</Tag> : <Tag>选填</Tag>}
                <Tag>{helpDef.value_type}</Tag>
                <Tag color="blue">{helpDef.stage_id}</Tag>
                {helpDef.advanced ? <Tag>高级</Tag> : null}
              </div>
            </div>
            <div>
              <Typography.Text type="secondary">默认值</Typography.Text>
              <Typography.Paragraph code style={{ marginBottom: 0, marginTop: 4 }}>
                {formatDefault(helpDef)}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Text type="secondary">说明</Typography.Text>
              <Typography.Paragraph style={{ marginBottom: 0, marginTop: 4 }}>
                {helpDef.description || "暂无说明"}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Text type="secondary">填写示例</Typography.Text>
              {(helpExtra?.examples || ["（暂无示例，可参考默认值）"]).map((ex) => (
                <Typography.Paragraph
                  key={ex}
                  code
                  copyable
                  style={{ marginBottom: 6, marginTop: 6, whiteSpace: "pre-wrap" }}
                >
                  {ex}
                </Typography.Paragraph>
              ))}
            </div>
            {helpExtra?.tips && helpExtra.tips.length > 0 && (
              <div>
                <Typography.Text type="secondary">注意</Typography.Text>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {helpExtra.tips.map((t) => (
                    <li key={t}>
                      <Typography.Text>{t}</Typography.Text>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(helpExtra?.up || helpExtra?.down || helpExtra?.off) && (
              <div>
                <Typography.Text type="secondary">改动效果</Typography.Text>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {helpExtra.up ? (
                    <li>
                      <Typography.Text>调大 / 打开：{helpExtra.up}</Typography.Text>
                    </li>
                  ) : null}
                  {helpExtra.down ? (
                    <li>
                      <Typography.Text>调小：{helpExtra.down}</Typography.Text>
                    </li>
                  ) : null}
                  {helpExtra.off ? (
                    <li>
                      <Typography.Text>关闭 / 留空：{helpExtra.off}</Typography.Text>
                    </li>
                  ) : null}
                </ul>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </Space>
  );
}
