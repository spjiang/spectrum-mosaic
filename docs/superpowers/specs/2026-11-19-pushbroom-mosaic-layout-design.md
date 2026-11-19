# 推扫拼图：目录与本地联调设计

日期：2026-11-19  
分支：`feature/pushbroom-mosaic`  
范围：目录、服务边界、任务协议和本机联调。行投影和航带镶嵌的算法另写。

## 1. 结论

- 平台只有一套：推扫的菜单、页面加在现有 `frontend`，接口、任务记录加在现有 `backend` 和 `db`。登录、角色、审计、数据根目录全部沿用。
- 只有推扫计算 worker 独立：自己的源码目录、自己的镜像、自己的队列。框幅 worker 镜像不因推扫改动重建，推扫算法更新也只换推扫 worker 镜像。
- 本阶段只在本机跑。不改 `release/`、`docker-compose.198.yml`、客户 compose 模板和启动脚本，不推送远端。
- 输入是原始 DN：`figspec.hdr`、`figspec.spe`、`figspec.gps`。第一版成果是地理编码 DN 立方体，不做暗电流、白板、反射率和 DSM。

## 2. 目录

```text
mosaic/server/
  frontend/                    现有。加推扫菜单和页面
  backend/                     现有。加推扫接口、任务表、进度消费
  db/migrations/               现有。加推扫的表和菜单授权迁移
  worker/                      现有框幅 worker，不动
  pushbroom-worker/            新增：推扫计算 worker，独立镜像
  data/                        本机数据根，容器内 /data
  docker-compose.yml           现有，不动
  docker-compose.gpu.yml       现有，不动
  docker-compose.pushbroom.yml 新增：本机叠加文件
```

### 2.1 前端改动

```text
frontend/src/
  roles.ts                     分组改为框幅拼图、推扫拼图、辅助工具、系统管理、组织管理；增加子组；新菜单键
  components/AppShell.tsx      三层侧栏；推扫路由
  pages/Roles.tsx              角色授权按新分组列出
  pages/pushbroom/
    PushbroomNew.tsx           选架次目录、核对三件套、提交
    PushbroomJobs.tsx          任务列表、进度、日志、成果下载、取消
  api.ts                       /api/pushbroom/* 调用，沿用现有 request()（401 跳登录）
```

前端已经把 `/api/` 代理到后端，`nginx.conf` 和 `vite.config.ts` 都不用改。

### 2.2 后端改动

```text
backend/app/
  models.py                    PushbroomJob
  schemas.py                   推扫请求与返回
  routers/pushbroom.py         /api/pushbroom/*
  services/pushbroom.py        路径校验、三件套快速核对、建任务、投递、取消、成果列表
  services/mq.py               增加 pushbroom.* 队列常量与投递方法；推扫 worker 在线探测
  services/progress_ws.py      增加消费 pushbroom.progress / pushbroom.events，写回 PushbroomJob
  services/rbac.py             MENU_KEYS 增加 pushbroom_new、pushbroom_jobs
  config.py                    PUSHBROOM_ENABLED，默认关闭
db/migrations/
  028_pushbroom.sql            pushbroom_jobs 表；已保存的角色菜单里，能新建处理的角色补上两个推扫菜单键
```

### 2.3 推扫 worker

```text
mosaic/server/pushbroom-worker/
  Dockerfile                   与框幅 worker 同样以 uid 10001 运行
  .dockerignore
  requirements.txt             numpy、pika、rasterio、pyproj 等。不装 OpenCV、CuPy
  pytest.ini
  README.md
  run.sh                       本机不起容器时直接听队列
  pb_mosaic/                   不导入 ms_mosaic，也不导入 backend/app
    __init__.py                版本号
    __main__.py                命令行：check / run，脱离队列调算法
    worker_main.py             听 pushbroom.jobs / pushbroom.control；任务在子进程里跑
    mq_pub.py                  回传 pushbroom.progress / pushbroom.events
    status.py                  心跳写 /data/.pushbroom-worker/status.json
    ingest/
      envi.py                  解析 ENVI 头；按 BIL 内存映射 .spe，不整块读入
      gps.py                   解析 figspec.gps
      flight.py                三件套完整核对，给出拒收原因
    geom/
      trajectory.py            按 Lines 把 1 秒一条的定位插值到每一行
      strips.py                按航向切航带，裁掉转弯
      footprint.py             行到地面的投影
      grid.py                  输出格网；默认按测区中心选 UTM 带，单位米
    mosaic/
      resample.py              单条航带按块重采样
      merge.py                 航带镶嵌，DN 原值不做匀色
    products/
      envi_out.py              成果立方体，.hdr 写 map info 与坐标系
      preview.py               三波段预览 PNG / GeoTIFF
      report.py                架次信息、航带、覆盖、拒收原因
  scripts/
    crop_real_flight.py        从真实架次裁一小段，输出到被忽略的目录，只供本机调试
  tests/
    conftest.py                现场生成合成小立方体与 gps，不往仓库放二进制
    test_envi.py
    test_gps.py
    test_flight_check.py       含 FigSpec_260410_142436 这类坏头、坏 gps
    test_trajectory.py
    test_strips.py
    test_merge.py
    test_worker_main.py
    test_real_flights.py       有本机原始数据时才跑，否则跳过
```

## 3. 任务协议

队列和框幅完全分开，框幅 worker 不声明、不消费这些队列：

| 队列 | 方向 | 内容 |
| --- | --- | --- |
| `pushbroom.jobs` | 后端 → worker | `schema_version`、`job_id`、`run_attempt`、`input_dir`、`output_dir`、`params` |
| `pushbroom.control` | 后端 → worker | 取消 |
| `pushbroom.progress` | worker → 后端 | 阶段、百分比、说明 |
| `pushbroom.events` | worker → 后端 | 开始、完成、失败（带原因）、成果清单 |
| `pushbroom.jobs.dlq` | | 反复失败的投递 |

阶段固定为：核对三件套、轨迹插值、切航带、航带重采样、镶嵌、写成果、报告。

worker 一次只跑一个推扫任务，先 ack 再起子进程，取消时杀子进程树，和框幅 worker 一样。

## 4. 校验分两层

- 后端提交时只做快的：路径在 `DATA_ROOTS` 下、三个文件都在、头文件是 ENVI 文本、`.spe` 字节数等于头里的行×列×波段×2。0410 这种头文件是 Mac 资源叉的，在提交时就拒绝，不进队列。
- worker 开算前做完整核对：gps 表头、`Lines` 落在立方体内、时间单调、航带能切出来。不通过就以失败结束，原因写进事件和报告。

## 5. 菜单

```text
框幅拼图
  新建处理        /execute
  处理方案        /profiles
  处理记录        /jobs
  拼接线处理      /seam
  增量拼图
    测区管理      /incremental
推扫拼图
  新建推扫        /pushbroom/new
  推扫记录        /pushbroom/jobs
辅助工具
  影像查看        /inspect
  使用文档        /cli
系统管理
  计算节点、系统监控、系统配置
组织管理
  用户管理、角色管理、权限管理、操作审计
```

- 现有菜单键都不改名，原有角色授权不变。
- `PUSHBROOM_ENABLED` 关闭时，后端不注册推扫接口，「推扫拼图」整组不显示，管理员也看不到。客户现场升级平台后默认就是关闭，行为和现在一样。
- 打开但推扫 worker 不在线时，菜单照常显示，页面顶部提示「推扫计算服务未在线，提交会排队」。在线判断看 `pushbroom.jobs` 的消费者数，和现有框幅 worker 的探测方式一致。
- 权限：提交、取消要求管理员或操作员；列表和成果下载只看自己的任务，管理员看全部。与框幅任务的规则相同。

## 6. 数据目录

```text
mosaic/server/data/
  input/
    pushbroom/<架次目录>/      例：FigSpec_260414_134505/{figspec.hdr,.spe,.gps}
  output/
    runs/                     现有框幅任务
    pushbroom/<任务编号>/
      products/               成果立方体 .hdr/.spe、预览 PNG/GeoTIFF
      process/                trajectory.csv、strips.geojson
      log/                    run.log、job_payload.json
      report/                 report.json / report.md
  .pushbroom-worker/          推扫 worker 心跳
```

- 原始架次每份 33 GiB 左右，不拷贝。叠加文件把 `docs/需求/无人机数据` 只读挂到 `/data/input/pushbroom`，后端和推扫 worker 两个容器都挂，路径由 `.env` 的 `DATASET_PUSHBROOM` 指定。
- 一份成果立方体约 17 GiB。页面给出盘上路径和预览图；立方体下载走流式响应，不经后端内存。

## 7. 资源

框幅 worker 和推扫 worker 可能在同一台机器上同时跑。推扫按航带、按块处理，内存峰值由 `PB_MAX_MEMORY_GB` 限定，本机叠加文件里给推扫 worker 设同样的 `mem_limit`。第一版不做两类任务之间的互斥。

## 8. 本机命令

```bash
cd mosaic/server

# 现有栈，和现在完全一样
docker compose up -d

# 带推扫：后端打开 PUSHBROOM_ENABLED，起 pushbroom-worker，挂原始架次
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d --build

# 推扫 worker 单测，不起容器
cd pushbroom-worker && PYTHONPATH=. pytest -q

# 在容器里直接跑一架，不经队列
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml exec pushbroom-worker \
  python -m pb_mosaic check --input /data/input/pushbroom/FigSpec_260414_134505
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml exec pushbroom-worker \
  python -m pb_mosaic run --input /data/input/pushbroom/FigSpec_260414_134505 \
                          --output /data/output/pushbroom/dev-0414
```

## 9. 以后进交付包

本阶段不做，只记下：

- 前端、后端的推扫改动随平台正常发版，`PUSHBROOM_ENABLED` 默认关闭。
- 新增 `release/build/Dockerfile.pushbroom-worker-customer` 和镜像 `spectrum-pushbroom-worker:<版本>-<架构>`；客户 compose 模板里是一个可选服务。
- 推扫 worker 也要做许可校验。它不能导入 `ms_mosaic`，许可校验的共用方式到交付时再定。

## 10. 验收

- 不带叠加文件启动时，现有页面、任务、增量和全部测试行为不变；侧栏只是换了分组，看不到「推扫拼图」。
- 带叠加文件时，侧栏是「框幅拼图 › 增量拼图 › 测区管理」三层，加「推扫拼图」。原有角色的授权不变，操作员能看到推扫两个菜单。
- `FigSpec_260414_134505`、`FigSpec_260419_132008`、`FigSpec_260429_134344`、`FigSpec_260429_135339` 四架核对通过；`FigSpec_260410_142436` 在提交时被拒，页面写明头文件和 gps 的问题。
- 推扫 worker 单测只靠现场生成的合成数据就能跑通；有原始数据时 `test_real_flights.py` 额外跑。
- 停掉 `pushbroom-worker` 后，框幅任务照常；推扫页面显示计算服务未在线，已提交的任务排队等待。
