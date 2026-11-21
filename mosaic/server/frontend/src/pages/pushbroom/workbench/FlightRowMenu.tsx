import { useState } from "react";
import { Button, Popover, Tooltip } from "antd";
import { DownOutlined, FileSearchOutlined, FolderOpenOutlined, PlusOutlined, QuestionCircleOutlined, ToolOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
import type { PushbroomFlightItem, PushbroomJob } from "../../../api";
import MountPickerModal from "../MountPickerModal";
import FlightInfoModal from "./FlightInfoModal";
import SubmitPushbroomModal from "./SubmitPushbroomModal";

type ActionTone = "primary" | "green" | "amber" | "danger";

type ActionItem = {
  key: string;
  label: string;
  help: string;
  icon: ReactNode;
  tone: ActionTone;
  disabled?: boolean;
  onClick: () => void;
};

/** 架次表操作列：分组弹窗，风格同框幅拼图 */
export default function FlightRowMenu({
  flight,
  admin,
  onOpenFlight,
  onSubmitted,
  onMountChanged,
}: {
  flight: PushbroomFlightItem;
  admin: boolean;
  onOpenFlight: () => void;
  onSubmitted: (job: PushbroomJob) => void;
  onMountChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  function run(fn: () => void) {
    setOpen(false);
    fn();
  }

  const work: ActionItem[] = [];
  if (flight.ok) {
    work.push(
      {
        key: "jobs",
        label: "任务列表",
        icon: <FolderOpenOutlined />,
        tone: "primary",
        help: "居中弹窗里查看这个架次的历次任务。",
        onClick: () => run(onOpenFlight),
      },
      {
        key: "submit",
        label: "提交拼图",
        icon: <PlusOutlined />,
        tone: "green",
        help: "提交新的推扫拼图任务，可指定分辨率与输出波段。",
        onClick: () => run(() => setSubmitOpen(true)),
      },
    );
  }

  const view: ActionItem[] = [
    {
      key: "info",
      label: "架次信息",
      icon: <FileSearchOutlined />,
      tone: "primary",
      help: flight.ok ? "飞行时间、数据大小、安装配置与目录。" : "查看核对失败原因与目录。",
      onClick: () => run(() => setInfoOpen(true)),
    },
  ];

  const config: ActionItem[] = [];
  if (admin && flight.ok) {
    config.push({
      key: "mount",
      label: "改选配置",
      icon: <ToolOutlined />,
      tone: "amber",
      help: "手动指定这个架次使用的安装配置，覆盖按日期自动匹配。",
      onClick: () => run(() => setPickerOpen(true)),
    });
  }

  const groups = [
    { key: "work", title: "工作台", items: work },
    { key: "view", title: "查看", items: view },
    { key: "config", title: "配置", items: config },
  ].filter((g) => g.items.length > 0);

  return (
    <>
      <Popover
        open={open}
        onOpenChange={setOpen}
        trigger="click"
        placement="bottomRight"
        getPopupContainer={() => document.body}
        overlayClassName="job-action-popover"
        content={
          <div className="job-action-card">
            {groups.map((group) => (
              <section className="job-action-group" key={group.key}>
                <div className="job-action-group-title">{group.title}</div>
                <div className="job-action-group-grid">
                  {group.items.map((item) => (
                    <div className={`job-action-tile is-${item.tone}`} key={item.key}>
                      <Button size="small" icon={item.icon} disabled={item.disabled} onClick={item.onClick}>
                        {item.label}
                      </Button>
                      <Tooltip title={item.help} placement="topLeft">
                        <button
                          type="button"
                          className="param-help-btn"
                          aria-label={`${group.title}${item.label}说明`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <QuestionCircleOutlined />
                        </button>
                      </Tooltip>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        }
      >
        <Button type="link" size="small">
          操作 <DownOutlined />
        </Button>
      </Popover>
      <FlightInfoModal flight={flight} open={infoOpen} onClose={() => setInfoOpen(false)} />
      {flight.ok && (
        <SubmitPushbroomModal
          flight={flight}
          open={submitOpen}
          onClose={() => setSubmitOpen(false)}
          onSubmitted={onSubmitted}
        />
      )}
      {admin && flight.ok && (
        <MountPickerModal flight={flight} open={pickerOpen} onClose={() => setPickerOpen(false)} onDone={onMountChanged} />
      )}
    </>
  );
}