import { useEffect, useState } from "react";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { Popover, Tooltip } from "antd";
import { api, type StageStep } from "../api";
import { formatStageSeconds, stageElapsedSeconds } from "../jobTime";
import { stepHelp } from "../stepHelp";
import {
  STAGE_META,
  STAGES,
  activeStageIndex,
  stageLabel,
  toneForStage,
  type JobStageLike,
  type StageId,
} from "../stages";

const TONE_TEXT: Record<string, string> = {
  wait: "未开始",
  process: "进行中",
  finish: "已完成",
  error: "失败",
  pause: "已暂停",
};

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function StepHelpButton({ name }: { name: string }) {
  const help = stepHelp(name);
  return (
    <Tooltip
      placement="topLeft"
      getPopupContainer={() => document.body}
      overlayClassName="job-stage-step-tooltip"
      title={
        <div className="job-stage-step-help">
          <p>
            <strong>做什么。</strong>
            {help.does}
          </p>
          <p>
            <strong>作用。</strong>
            {help.role}
          </p>
        </div>
      }
    >
      <button
        type="button"
        className="job-stage-step-help-btn"
        aria-label={`${name}说明`}
        onClick={(event) => event.stopPropagation()}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <QuestionCircleOutlined />
      </button>
    </Tooltip>
  );
}

function stepFacts(detail: string): string[] {
  return detail
    .split(/\s*·\s*|，/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function StageDetail({ job, stage }: { job: JobStageLike; stage: StageId }) {
  const [steps, setSteps] = useState<StageStep[] | null>(null);
  const [error, setError] = useState("");
  const live = job.status === "running";
  useEffect(() => {
    if (!job.id) return;
    let stop = false;
    const load = () => {
      api.stageSteps(job.id as string)
        .then((res) => {
          if (!stop) {
            setSteps(res.stages?.[stage] || []);
            setError("");
          }
        })
        .catch(() => {
          if (!stop) setError("暂时读不到这一步的明细");
        });
    };
    load();
    if (!live) return;
    const id = window.setInterval(load, 5000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [job.id, stage, live]);
  const meta = STAGE_META[stage];
  if (!job.id) return <div className="job-stage-detail-empty">没有任务编号，读不到明细。</div>;
  if (error) return <div className="job-stage-detail-empty">{error}</div>;
  if (steps === null) return <div className="job-stage-detail-empty">正在读取…</div>;
  if (!steps.length) {
    return (
      <div className="job-stage-panel">
        <p className="job-stage-panel-hint">{meta.hint}</p>
        <div className="job-stage-detail-empty">{toneForStage(job, stage) === "wait" ? "这一步还没开始。" : "日志里还没有这一步的分项。"}</div>
      </div>
    );
  }
  return (
    <div className="job-stage-panel">
      <p className="job-stage-panel-hint">{meta.hint}</p>
      <ol className="job-stage-detail">
        {steps.map((step, i) => {
          const time = formatStageSeconds(step.elapsed_s);
          const facts = stepFacts(step.detail);
          const running = step.status === "running";
          return (
            <li key={`${step.name}-${i}`} className={running ? "is-running" : "is-done"}>
              <div className="job-stage-step-main">
                <span className="job-stage-step-title">
                  <span className="job-stage-step-name">{step.name}</span>
                  <StepHelpButton name={step.name} />
                </span>
                {running ? <span className="job-stage-step-badge">进行中</span> : null}
                <span className="job-stage-step-time">{time === "—" ? "" : running ? `已进行 ${time}` : time}</span>
              </div>
              {facts.length > 0 && (
                <div className="job-stage-step-facts">
                  {facts.map((fact) => (
                    <span key={fact} className="job-stage-step-fact">{fact}</span>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function StageNodes({ job, variant }: { job: JobStageLike; variant: "full" | "compact" }) {
  const live = job.status === "running";
  const now = useNow(variant === "full" && live);
  return (
    <div className={`job-stage-flow is-${variant}`} role="list" aria-label="流程节点">
      {STAGES.map((stage, i) => {
        const meta = STAGE_META[stage as StageId];
        const tone = toneForStage(job, stage as StageId);
        const elapsed = variant === "full" ? stageElapsedSeconds(job, stage as StageId, now) : null;
        const timeText = variant !== "full" || tone === "wait" ? "" : formatStageSeconds(elapsed);
        return (
          <div key={stage} className="job-stage-item" role="listitem">
            {i > 0 && <div className={`job-stage-line is-${tone}`} />}
            <Popover
              trigger="click"
              placement="bottomLeft"
              title={meta.label}
              overlayClassName="job-stage-popover"
              destroyTooltipOnHide
              getPopupContainer={() => document.body}
              content={<StageDetail job={job} stage={stage as StageId} />}
            >
              <button
                type="button"
                className={`job-stage-node is-${tone}`}
                title={`${meta.label} · ${TONE_TEXT[tone]}${timeText ? ` · 耗时 ${timeText}` : ""} · 点击查看分项`}
                onClick={(e) => e.stopPropagation()}
              >
                <span className="job-stage-index">{i}</span>
                <span className="job-stage-name">{variant === "compact" ? meta.short : meta.label}</span>
                {variant === "full" && <span className="job-stage-time">{timeText || "\u00a0"}</span>}
              </button>
            </Popover>
          </div>
        );
      })}
    </div>
  );
}

function summary(job: JobStageLike) {
  const idx = activeStageIndex(job);
  if (idx < 0) {
    return { index: "—", label: job.status === "queued" ? "排队中" : "未开始", tone: "wait" as const };
  }
  const id = STAGES[idx] as StageId;
  return {
    index: String(idx),
    label: STAGE_META[id].label,
    tone: toneForStage(job, id),
  };
}

export default function JobStageFlow({
  job,
  variant = "full",
}: {
  job: JobStageLike;
  variant?: "full" | "compact";
}) {
  if (variant !== "compact") {
    return <StageNodes job={job} variant="full" />;
  }

  const s = summary(job);
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      title="流程节点"
      destroyTooltipOnHide
      getPopupContainer={() => document.body}
      content={<StageNodes job={job} variant="full" />}
    >
      <button type="button" className="job-stage-summary" onClick={(e) => e.stopPropagation()}>
        <span className={`job-stage-index is-${s.tone}`}>{s.index}</span>
        <span className="job-stage-summary-label">{s.label}</span>
        <span className={`job-stage-summary-tone is-${s.tone}`}>{TONE_TEXT[s.tone]}</span>
        <span className="job-stage-summary-more">详情</span>
      </button>
    </Popover>
  );
}

export function JobStageCaption({ job }: { job: JobStageLike }) {
  return <span>{stageLabel(job.current_stage || job.completed_stage)}</span>;
}
