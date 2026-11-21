import { Popover, Typography } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";

const PRODUCT_HELP: { name: string; text: string }[] = [
  { name: "*_mosaic.spe", text: "拼好的高光谱立方体，ENVI BIL。数值是相机原始 DN，没有做过显示拉伸。" },
  { name: "*_mosaic.hdr", text: "立方体的头文件。行列数、波段、波长和坐标都在这里，必须和 spe 放在同一个目录。" },
  { name: "*_rgb.tif", text: "红绿蓝三个波段的 GeoTIFF，带坐标。数值仍是原始 DN，适合继续计算，直接打开会偏暗。" },
  { name: "*_rgb_display.tif", text: "同一张 RGB，做过亮度拉伸。颜色接近屏幕上的预览，用来目视，不能当作反射率。" },
  { name: "*_preview.png", text: "页面上的结果预览。内容和显示拉伸图一致，没有地理坐标。" },
  { name: "*_footprints.geojson", text: "每条航带落到成果上的范围，可在 GIS 里查看航带是怎么拼上去的。" },
  { name: "report.json", text: "处理报告，给程序读。里面有分辨率、覆盖面积、配准、标定提示和各步耗时。" },
  { name: "report.md", text: "同一份报告的文字版，用文本编辑器直接打开。" },
];

export default function ProductHelp() {
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      title="成果文件说明"
      getPopupContainer={() => document.body}
      content={
        <div style={{ width: 380 }}>
          {PRODUCT_HELP.map((item) => (
            <div key={item.name} style={{ marginBottom: 10 }}>
              <Typography.Text code>{item.name}</Typography.Text>
              <div style={{ marginTop: 2 }}>{item.text}</div>
            </div>
          ))}
        </div>
      }
    >
      <button type="button" className="param-help-btn" aria-label="成果文件说明" onClick={(e) => e.stopPropagation()}>
        <QuestionCircleOutlined />
      </button>
    </Popover>
  );
}