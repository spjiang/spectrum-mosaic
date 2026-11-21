import { Tag } from "antd";
import type { PushbroomMountRef } from "../../api";

/** 架次对应的安装配置。没有对应配置时用橙色提醒。 */
export default function MountTag({ mount }: { mount: PushbroomMountRef }) {
  if (mount.source === "builtin") return <Tag color="orange">内置（没有对应配置）</Tag>;
  return (
    <Tag color="blue">
      {mount.name}
      {mount.source === "manual" ? " · 手动" : ""}
    </Tag>
  );
}