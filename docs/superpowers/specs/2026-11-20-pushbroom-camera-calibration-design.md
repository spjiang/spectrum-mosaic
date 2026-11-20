# 推扫相机标定：页面与生效方式

日期：2026-11-20  
分支：`feature/pushbroom-mosaic`  
范围：系统级相机安装标定的页面、接口和任务注入。不改投影算法，不改飞控数据。

## 1. 结论

沿航向偏移 `along_offset_m`、狭缝中心偏置 `boresight_px` 描述相机相对定位点的安装，不是飞控参数，也不是每次作业参数。

- 管理员在「相机标定」页选择架次，运行已有 `calibrate`，只读查看建议值，确认后应用到之后新建的推扫任务。
- 数字不可手填、不可微调。
- 写入数据库。创建任务时由后端注入 `params`。不改 `.env`，不重启 `pushbroom-worker`。
- 正在计算和已完成的任务保持各自已保存的 `params`。重跑也沿用该任务的 `params`。
- 未标定则不注入这两个字段。worker 现有优先级不变：任务参数 > 环境变量 `PB_SENSOR_*` > 内置值（1.7 m，−19 像元）。

## 2. 页面

菜单在「推扫拼图」下，键 `pushbroom_calibration`，路径 `/pushbroom/calibration`，标签「相机标定」。`PUSHBROOM_ENABLED` 关闭时与其它推扫菜单一起隐藏。

默认只授给管理员：写入 `MENU_KEYS` 与 `DEFAULT_MENUS["admin"]`，不放入 `GRANT_WITH_EXECUTE`，也不写进操作员默认菜单。已保存过角色菜单的库，管理员一行由 `load_mapping` 固定为全部 `MENU_KEYS`，因此升级后管理员能看到；操作员已保存的列表不会被补上这一键。接口另行要求当前用户角色含 `admin`，仅有菜单键不够。操作员和只读用户调用返回 403。

### 2.1 当前生效

页顶说明卡，只读：

| 项 | 未标定 | 已标定 |
| --- | --- | --- |
| 沿航向偏移 | 内置 1.70 m | 记录中的米值，一位或两位小数 |
| 狭缝中心偏置 | 内置 −19 像元 | 记录中的像元值，一位小数 |
| 来源 | 尚未标定，使用内置值 | 相机标定：日期、操作人、架次名 |

说明文案固定为：这两个值描述相机相对定位点的安装，不是飞控参数。拆装或更换相机后重新标定。

### 2.2 标定

架次表与新建推扫页使用同一目录列表，只列出核对通过的架次，可多选。未选时「开始标定」禁用。

计算服务不在线时，「开始标定」禁用，页顶警告与新建页相同：「推扫计算服务未在线」。

运行中按钮处于 loading。提示：每个架次大约 1 分钟，请留在本页。

返回后：

- `usable: true`：只读展示合并值及不确定度（例如 `1.78 ± 0.11 m`、`−50 ± 3 像元`），并列出每个架次各自的建议值。主按钮「应用到之后的任务」。
- `usable: false` 或单个架次失败：展示 `combined.note` 和各架次错误或建议值。不显示应用按钮。

没有数字输入框。

### 2.3 应用

确认框文案：「之后新建的推扫任务使用这组值。正在计算和已完成的任务不变。」

确认后保存。页顶「当前生效」改为这组值。成功提示：「已保存。之后新建的任务使用这组值，无需重启计算服务。」

应用按钮只对当前这一次运行结果有效。刷新页面、改选架次或再次点击「开始标定」后，须重新得到 `usable: true` 才能应用。

### 2.4 任务侧

新建推扫页不增加这两个字段。

任务详情在参数区增加两行：沿航向偏移、狭缝中心偏置。`params` 里有值则显示该值，并注明「本任务标定」；没有则显示「内置」。

任务详情若能读到成果报告且 `warnings` 含标定不符文案，显示警告条：「本架次与当前标定不符，航带接缝可能错开。」管理员看到链接「去标定」，指向 `/pushbroom/calibration`。警告不自动改标定，也不改该任务的 `params`。

## 3. 数据

新迁移 `029_pushbroom_calibration.sql`。

```text
pushbroom_calibrations
  id              UUID 主键
  along_offset_m  DOUBLE PRECISION NOT NULL
  boresight_px    DOUBLE PRECISION NOT NULL
  along_se_m      DOUBLE PRECISION NOT NULL
  boresight_se_px DOUBLE PRECISION NOT NULL
  flights         JSONB NOT NULL          -- 架次名字符串数组
  created_by      INTEGER NULL            -- users.id
  created_at      TIMESTAMPTZ NOT NULL
```

不设「仅一条」的数据库约束。生效记录定义为 `created_at` 最新的一行。历史行保留，供审计对照；页面只展示最新一行。

没有行表示未标定。

## 4. 接口

均在现有 `/api/pushbroom` 下，沿用推扫开关：未开启返回 404。均要求管理员。

### 4.1 读取

`GET /api/pushbroom/calibration`

未标定：

```json
{ "active": false, "builtin": { "along_offset_m": 1.7, "boresight_px": -19 } }
```

已标定：`active: true`，并带 `along_offset_m`、`boresight_px`、`along_se_m`、`boresight_se_px`、`flights`、`created_at`、`created_by`（用户名）。

### 4.2 运行

`POST /api/pushbroom/calibration/run`

请求：`{ "input_dirs": ["..."] }`，1～8 个目录。每个目录走现有推扫路径校验，且须通过三件套快核。计算服务不在线返回 409，正文说明未在线。

后端在推扫 worker 容器内执行：

```text
python -m pb_mosaic calibrate --input <目录> [--input <目录> ...]
```

不传 `--params`，让标定按 worker 当前内置值（及该容器环境变量）反推建议值。超时为 `架次数 × 120` 秒，上限 600 秒。超时或非零退出且 stdout 不是合法 JSON：返回 502，不落库。进程退出码为 1 但 stdout 是合法标定 JSON（`usable: false`）时，按 200 返回该 JSON，由页面禁止应用。

响应体为 calibrate 的 JSON：`flights`、`combined`；可用时另有 `env`。后端不改写数值。`usable: true` 时另外附上 `run_token`。token 为 JWT，`typ` 为 `pushbroom_calibration`，用 `settings.jwt_secret` 与 `settings.jwt_algorithm` 签名（与下载凭证相同的密钥，类型字段不同，不能当作登录或下载凭证）。payload 含 `along_offset_m`、`boresight_px`、`along_se_m`、`boresight_se_px`、`flights`（架次名数组，顺序与 calibrate 的合并输入一致）和 `exp`。有效期 30 分钟。服务端不另存运行结果。`usable: false` 时不签发 token。

### 4.3 应用

`POST /api/pushbroom/calibration`

请求字段为 `along_offset_m`、`boresight_px`、`along_se_m`、`boresight_se_px`、`flights` 和 `run_token`。服务端解码 JWT，核对 `typ`，再逐项比对正文与 token 内的数值和架次名（浮点按标定输出的舍入：偏移和其标准误差 2 位小数，像元和其标准误差 1 位小数）。过期或签名失败按下载凭证的方式返回 400（文案为「标定结果已失效，请重新标定」）。另校验数值范围：

- `along_offset_m`：−20～20
- `boresight_px`：−400～400
- 两个标准误差 ≥ 0
- `flights` 为 1～8 个非空字符串

不通过返回 400。通过则插入一行，写审计 `pushbroom.calibration.apply`（操作人、两个值、架次名）。响应与 GET 已标定形态相同。

相同数值再次应用允许，新插入一行，更新时间与操作人。

不接受没有 `run_token` 的手填数字。

## 5. 任务注入

`create_job` 在通过现有校验之后、写入 `params` 之前：

- 若请求 `params` 含 `along_offset_m` 或 `boresight_px`，返回 400：「安装参数由相机标定决定，不能在任务里填写」。
- 若存在生效标定，把这两个数值写入即将保存的 `params`（保留调用方合法的 `gsd_m`、`bands`）。
- 若无生效标定，不写入这两个键。

`retry` 重投原 `params`，不读取最新标定。

worker 不改 `sensor_from` 的优先级。报告里的「当前」因此就是该任务注入值或内置值；「建议」仍由本架次反推。两者相差超过现有警告阈值时，报告警告逻辑不变。

## 6. 失败

| 情况 | 行为 |
| --- | --- |
| 计算服务不在线 | run 返回 409；页面禁用开始标定 |
| 超时、进程崩溃、stdout 非 JSON | 502；不落库；页面保留已选架次并显示原因 |
| `usable: false` 或单架次错误 | HTTP 200 + JSON；无应用按钮 |
| token 过期或不匹配 | 应用返回 400；须重新标定 |
| 非管理员 | 403 |
| 推扫未开启 | 404 |

## 7. 测试

后端：

- 无标定记录时创建任务，`params` 不含 `along_offset_m`、`boresight_px`。
- 写入一条标定后创建任务，`params` 含该两值；请求里自带这两键返回 400。
- 重跑任务不替换为更新的标定。
- `run_token` 与正文不一致、过期、类型不对、或缺 token 时应用失败。
- 非管理员调用三个接口为 403。
- 标定子进程以假 JSON 代替真实架次：`usable: false` 时不写表；合法 token 应用后 GET 为 `active: true`。

不在本规格的测试里跑真实大架次。`calibrate` 算法仍由 pushbroom-worker 现有单测覆盖。

前端：标定页在现有组件测试习惯下覆盖三种状态文案——未标定、建议值只读且可应用、`usable: false` 时无应用按钮。若当前前端没有页面测试，则不新引入测试框架，改为在后端契约测试里锁定上述 JSON 字段，页面按第 2 节实现并由人工点选核对。

## 8. 不做

- 不在新建任务页提供覆盖或「按本架次建议值」。
- 不把结果写入 `.env`，不重启容器。
- 不增加手填或微调。
- 不自动用报告警告覆盖系统标定。
- 不改 `release/` 打包（本阶段仍只在本机 `feature/pushbroom-mosaic`）。客户交付包若以后要带此页，另开规格。
