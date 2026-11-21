import { createElement } from "react";
import { Checkbox, Flex, Modal, Typography, message } from "antd";
import { api, type PushbroomJob } from "../../../api";
import { formatNo } from "../../../ids";

async function run(fn: () => Promise<unknown>, ok: string, after: () => void) {
  try {
    await fn();
    message.success(ok);
    after();
  } catch (err) {
    message.error((err as Error).message || String(err));
  }
}

export function retryJob(job: PushbroomJob, after: () => void) {
  return run(() => api.pushbroomRetry(job.id), "已重新提交", after);
}

export function confirmCancel(job: PushbroomJob, after: () => void) {
  Modal.confirm({
    title: `取消推扫任务 ${formatNo(job.seq)}？`,
    content: job.status === "running" ? "正在计算的进程会被停止，已写出的中间文件保留在成果目录。" : "任务还在排队，取消后不会开始计算。",
    okText: "取消任务",
    okButtonProps: { danger: true },
    cancelText: "返回",
    onOk: () => run(() => api.pushbroomCancel(job.id), "已发出取消", after),
  });
}

export function confirmDelete(job: PushbroomJob, after: () => void) {
  let withFiles = false;
  Modal.confirm({
    title: `删除推扫任务 ${formatNo(job.seq)}？`,
    content: createElement(
      Flex,
      { vertical: true, gap: 8 },
      createElement("span", null, "删除任务记录和日志。"),
      createElement(
        Checkbox,
        { onChange: (e) => (withFiles = e.target.checked) },
        "同时删除成果目录 ",
        createElement(Typography.Text, { code: true }, job.output_dir),
      ),
    ),
    okText: "删除",
    okButtonProps: { danger: true },
    cancelText: "返回",
    onOk: () =>
      run(() => api.pushbroomDelete(job.id, withFiles), withFiles ? "任务和成果已删除" : "任务记录已删除，成果文件保留", after),
  });
}