# 推扫拼图：目录与本地联调设计

日期：2026-11-19  
分支：`feature/pushbroom-mosaic`  
范围：只定目录、镜像边界和本机联调方式。算法细节另写。

## 1. 约束

- 推扫拼图是独立服务、独立镜像，单独发版。框幅拼图的 `backend`、`worker` 镜像不因推扫改动而重建。
- 本阶段只在本机跑。不改 `release/`，不改 `docker-compose.198.yml`，不改客户 compose 模板和启动脚本，不推送远端。
- 客户前端镜像由 `release/build/build_worker.sh` 直接从 `mosaic/server/frontend` 构建。这个目录里的 `nginx.conf` 本阶段不改；菜单代码若要进前端，必须在没有推扫服务时自动隐藏（见第 5 节）。
- 输入是原始 DN：`figspec.hdr`、`figspec.spe`、`figspec.gps`。第一版成果是地理编码 DN 立方体，不做暗电流、白板、反射率和 DSM。

## 2. 放在哪里

推扫和 `backend`、`worker`、`frontend` 并列，放在 `mosaic/server/pushbroom/`：

```text
mosaic/server/
  backend/                    现有，不动
  worker/                     现有，不动
  frontend/                   现有，第 5 节之前不动
  db/                         现有框幅库表，不放推扫表
  data/                       本机数据根，容器内 /data
  pushbroom/                  新增：推扫服务，独立镜像
  docker-compose.yml          现有，不动
  docker-compose.gpu.yml      现有，不动
  docker-compose.pushbroom.yml  新增：本机叠加，不带它启动时和现在完全一样
```

放在 `server/` 下而不是另起顶层目录：共用同一份 `data/`、同一个 Postgres 和同一套 compose 项目名，本机联调只多一个 `-f`。镜像仍然独立，构建上下文只有 `pushbroom/` 自己。

## 3. 推扫服务内部

```text
mosaic/server/pushbroom/
  Dockerfile
  .dockerignore
  requirements.txt
  pytest.ini
  README.md                   本机启动、命令行、测试
  pb_mosaic/                  Python 包。和 ms_mosaic 不互相导入
    __init__.py               版本号
    __main__.py               命令行：check / run
    config.py                 环境变量：DATA_ROOTS、DATABASE_URL、JWT_SECRET 等
    ingest/
      envi.py                 解析 ENVI 头；按 BIL 内存映射 .spe，不整块读入
      gps.py                  解析 figspec.gps（Lines、时间、经纬高）
      flight.py               三件套核对：头可读、字节数相符、gps 有表头且行号落在立方体内
    geom/
      trajectory.py           按 Lines 把 1 秒一条的定位插值到每一行
      strips.py               按航向切航带，裁掉转弯
      footprint.py            行到地面的投影（航高、像元、焦距）
      grid.py                 输出格网与坐标系
    mosaic/
      resample.py             单条航带重采样到格网
      merge.py                航带镶嵌，DN 原值不做匀色
    products/
      envi_out.py             写成果立方体（.hdr 带 map info / 角点）
      preview.py              三波段预览 PNG / GeoTIFF
      report.py               架次信息、航带、覆盖、拒收原因
    service/
      app.py                  FastAPI，前缀 /pushbroom/api
      auth.py                 校验平台签发的 JWT，不另建账号
      routes.py               健康、架次核对、提交、任务列表、日志、成果下载
      store.py                任务表读写
      runner.py               同时只跑一个任务；计算在子进程里，取消时杀子进程
  migrations/
    001_init.sql              schema pushbroom，表 pushbroom.jobs 等。服务启动时自己执行
  web/                        推扫页面（Vite + React），构建后由本镜像在 /pushbroom/ 提供
    package.json
    vite.config.ts            base: /pushbroom/
    src/
  dev/
    nginx.frontend.conf       仅本机：整份替换前端容器的 default.conf，多一段 /pushbroom/ 反代
  scripts/
    make_fixture.py           从真实架次裁小样本（几百行、少量波段）
    test.sh                   本机跑 pytest
  tests/
    fixtures/                 合成小样本，入库，几 MB 以内
    test_envi.py
    test_gps.py
    test_flight_check.py      含 FigSpec_260410_142436 这类坏头、坏 gps 的拒收
    test_trajectory.py
    test_strips.py
    test_merge.py
    test_api.py
```

边界：

- `pb_mosaic` 不导入 `ms_mosaic`，也不导入 `backend/app`。共用的东西只有 JWT 密钥、Postgres 连接和 `/data` 路径约定。
- 库表放在独立 schema `pushbroom`，迁移文件跟着推扫镜像走。`mosaic/server/db/` 和框幅的 `init.sql` 不加推扫表。
- 不用 RabbitMQ，也不碰 `mosaic.jobs`。任务排队就在 `pushbroom.jobs` 表里，服务进程里单线程取任务。
- 一个镜像两种用法：`python -m pb_mosaic.service` 起接口和任务；`python -m pb_mosaic run --input … --output …` 在本机或容器里直接跑一架，用来调算法。

## 4. 数据目录

输入和输出都放在现有 `server/data/` 下，单独一层 `pushbroom/`，和框幅测区分开：

```text
mosaic/server/data/
  input/
    pushbroom/<架次目录>/     例：FigSpec_260414_134505/{figspec.hdr,.spe,.gps}
  output/
    runs/                    现有框幅任务
    pushbroom/<任务编号>/
      products/              成果立方体 .hdr/.spe、预览 PNG/GeoTIFF
      process/               trajectory.csv、strips.geojson
      log/                   run.log、job.json
      report/                report.json / report.md
```

这批原始架次每份 33 GiB 左右，不拷贝。`docker-compose.pushbroom.yml` 把 `docs/需求/无人机数据` 只读挂到 `/data/input/pushbroom`，路径由 `.env` 的 `DATASET_PUSHBROOM` 指定。`docs/需求/无人机数据/` 和 `server/data/input/**`、`output/**` 本来就在 `.gitignore` 里。

## 5. 平台入口

本机联调时：

- `docker-compose.pushbroom.yml` 新增 `pushbroom` 服务（端口 8100），并给现有 `frontend` 容器额外挂载 `pushbroom/dev/nginx.frontend.conf` 覆盖 `default.conf`。原 `frontend/nginx.conf` 不改。
- 访问 `http://localhost:9080/pushbroom/` 进入推扫页面，接口走 `/pushbroom/api/`。和平台同源，登录后浏览器里的 token 直接可用。

框幅前端加菜单放到最后一步，条件是：

- 菜单只在 `GET /pushbroom/api/health` 返回推扫服务自己的 JSON 时显示。客户现场没有这个容器时，nginx 会把这个地址落到 `index.html`，判断为不可用，菜单不出现。
- 第一版不改后端 `rbac.py` 的菜单表，按现有「能新建处理」的角色显示。要进角色管理时再加菜单键。

## 6. 本机命令

```bash
cd mosaic/server
# 现有栈，不带推扫
docker compose up -d

# 带推扫
docker compose -f docker-compose.yml -f docker-compose.pushbroom.yml up -d --build

# 只测推扫，不起容器
cd pushbroom && ./scripts/test.sh

# 命令行跑一架
python -m pb_mosaic check --input /data/input/pushbroom/FigSpec_260414_134505
python -m pb_mosaic run   --input /data/input/pushbroom/FigSpec_260414_134505 \
                          --output /data/output/pushbroom/dev-0414
```

## 7. 以后进交付包

本阶段不做，只记下位置：`release/build/Dockerfile.pushbroom-customer`、构建脚本里多出一个 `spectrum-pushbroom:<版本>-<架构>` 镜像、客户 compose 模板里一个可选服务和一份带 `/pushbroom/` 的前端 nginx 配置。推扫后续更新只换这一张镜像。

## 8. 验收

- 不带叠加文件启动时，现有页面、任务、增量和测试行为不变。
- `scripts/test.sh` 只用 `tests/fixtures` 就能跑通，不依赖 33 GiB 原始数据。
- `check` 对 0414、0419、0429 两架通过，对 `FigSpec_260410_142436` 拒收并写明头文件和 gps 的问题。
- 带叠加文件时，`/pushbroom/` 页面能用平台账号登录后的 token 调接口；停掉 `pushbroom` 容器后，平台其它页面不受影响。
