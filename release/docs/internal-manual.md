# 发版台内部操作手册

本文只给发版人员使用，在发版台里阅读。打交付包时**不会**附带本文。给客户的是「操作手册」。

---

## 1. 先分清两套系统

| | 发版台 | 客户交付包 |
| --- | --- | --- |
| 谁用 | 我方 | 客户现场 |
| 跑什么 | 页面、发版接口、PostgreSQL | 拼图页面、任务接口、worker、数据库、消息队列 |
| 有没有算法源码 | 有，用来编译客户镜像 | 没有对应的 `.py` |
| 有没有签发私钥 | 有，只在发版台 | 没有。镜像里只有验签公钥 |
| 默认地址 | `http://<发版台>:8180` | `http://<客户机>:9080`（以包内 `.env` 为准） |

客户机器归客户管。目录权限挡不住拷贝，所以不把算法明文和私钥交出去。

---

## 2. 架构

```text
我方发版台
  页面 :8180
    └─ /api/ 转发到后端 :8100
  后端
    ├─ 版本构建：调用本机 Docker，编译并导出客户镜像
    ├─ 用私钥签发 .lic，以及客户来领的短时租约
    └─ 把镜像、许可、操作手册打成 zip
  PostgreSQL：客户、版本、许可、交付记录
  release/keys/：Ed25519 私钥和公钥

客户机器
  页面 / 任务接口 / worker / PostgreSQL / RabbitMQ
  worker 启动
    ├─ 用镜像里的公钥验证 licenses/*.lic
    └─ 若许可写了机器指纹，再向发版台领取租约
         POST {LICENSE_SERVER_URL}/api/license/check
```

`LICENSE_SERVER_URL` 填发版台根地址，不要带路径，例如 `http://123.56.26.229:8180`。worker 自己接上 `/api/license/check`。这个接口在发版台后端，经页面的 nginx 转发，不需要登录。

发版台页面端口是 **8180**。云服务器要在安全组放行 TCP 8180，外网才能打开页面和这个接口。后端 8100 只监听本机，不对公网开放。

---

## 3. 保护原理

这里不是「把源码加密，再在客户机器上解密」。程序必须在客户机器上运行，解密密钥如果也在那台机器上，对方可以一起拷走。

实际做两件事：

**1. 算法不交付明文**

构建客户 worker 镜像时，把 `ms_mosaic` 里列出的算法模块用 Cython 编译成 `.so`，并删除对应的 `.py`。清单在 `release/build/cython_modules.txt`，包括匹配、正射、融合、DSM、GPU 等。构建结束会检查 `pipeline.py` 不在镜像里。

启动入口 `worker_main.py` 和验签程序 `license_check.py` 仍是 Python。它们不包含拼图算法，但要在客户机器上判断许可。页面和任务接口也不做这层编译，算法不在那两侧。

**2. 没有有效许可就不能算**

许可是一段 JSON，加上 Ed25519 签名。客户改任何一个字段，签名对不上，worker 拒绝启动。开发环境默认不强制验许可。客户镜像设置 `MOSAIC_REQUIRE_LICENSE=1`，启动时必须验。

能挡住的是：直接打开文件抄算法、自己改到期日、把已绑定的许可拷到另一台机器。挡不住的是：客户有 root，且会逆向机器码或改验签入口。这是提高直接盗用的成本，不是在对方完全控制的机器上做到不可破解。

---

## 4. 公钥和私钥怎么用

密钥在发版台的 `release/keys/`：

| 文件 | 谁拿着 | 用来做什么 |
| --- | --- | --- |
| `ed25519_private.pem` | 只在发版台 | 签发 `.lic`；客户来领租约时再签一份短时租约 |
| `ed25519_public.pem` | 构建客户镜像时打进 worker | 客户机器上验 `.lic` 和租约。没有私钥，验签通过也不能改内容再签 |

第一次签发时如果目录里还没有密钥，发版台会自动生成一对。私钥不进 git，不进客户包，不进客户镜像。

换私钥意味着以前发出的许可和镜像里的公钥对不上，旧许可全部失效。不要随意删 `release/keys/`。迁发版台时，密钥目录和数据库要一起带走。

租约同样是这对密钥：发版台用私钥签，worker 用镜像里的公钥验。租约最长 72 小时，且不会超过许可到期日。

---

## 5. 发版操作顺序

按菜单从左到右做。

**① 版本构建**

登记版本号，并选择 CPU 架构（`amd64` 或 `arm64`）。同一版本号可以各构建一次，交付包不能混用架构。

**amd64** 的客户 worker 按 GPU 镜像构建（CUDA 12.2 + CuPy），包内另有 `docker-compose.gpu.yml`。客户机有 NVIDIA 且 Docker 能传 GPU 时，启动脚本自动叠加上去；没有卡或传失败则走 CPU，服务仍能起来。**arm64** 仍是 CPU 镜像。不要把 `gpus` 写进默认 `docker-compose.yml`，否则没卡的机器 `compose up` 会失败。

构建会编译 worker、导出页面、接口、worker、PostgreSQL、RabbitMQ 五份离线镜像。状态变为成功后才能打交付包。失败时在本页看日志。GPU 版 worker 比 CPU 版大，构建更久。

**② 客户档案**

填写单位名称、联系人、状态（试用 / 正式 / 停用）。许可和交付包都要选客户。停用的客户不能新签发。

**③ 许可签发**

见第 6 节。没有许可就打不出交付包。

**④ 交付包**

选择客户、已构建成功的版本、一条有效许可，生成 zip 并下载。包是打那一刻许可文件的副本。之后在「许可签发」里改指纹或到期日，已经下载的 zip **不会**自动更新，需要把新的 `.lic` 另行交给客户替换。

**⑤ 操作手册**

打 zip 时放进两份拼图服务手册，来源是仓库 `docs/操作文档/`：

- `docs/拼图服务私有部署操作手册.md`
- `docs/拼图服务WEB操作手册.md`

不要把本内部手册放进去。发版台页面上的「客户操作手册」仍是 `release/docs/customer-manual.md`，不打进交付包。

---

## 6. 许可签发怎么操作

列表「机器」列为空时显示「未绑定」。现在有两种许可。

**未绑定**

签发时没有写机器指纹。客户机器只验签名、生效日、到期日和时钟，不核对是哪一台机器，也不向发版台领租约。这种许可可以先打进交付包再安装。编辑时指纹可以继续留空。

**已绑定**

许可里写了机器指纹，即客户机器 `/etc/machine-id` 的 SHA-256，64 位十六进制。必须和本机一致，并在启动时以及之后每 6 小时向发版台领取租约。

- 发版台拒绝（已停用、机器不对、未生效、已过期）：worker 立刻退出，并清掉本机租约。
- 暂时连不上发版台：本机还有未过期租约就可以继续算；没有租约则不能启动。
- 每次领取（成功或被拒绝）都写入发版台数据库，在「租约日志」里按时间倒序分页查看。记下许可、客户、机器指纹、结果、租约到期时间和来源地址。
- 换机器：在发版台改指纹并保存（会重新签名），把新 `.lic` 交给客户替换。已下载的 zip 不会跟着变。

**新签发**

点「签发许可」，选择客户、版本、生效时间、到期时间。**机器指纹可留空**（未绑定），先打交付包；客户在机器上运行 `start.sh` / `start.ps1` 得到 `machine_id` 后发回，再在「编辑许可」填写指纹并保存（会重新签名），把新 `.lic` 交给客户。已知指纹时也可在签发阶段直接填写。

**把未绑定许可绑上机器**

在列表里点该许可的「编辑」，填入 64 位指纹，保存。文件会按原 `license_id` 重新签名。再点「下载」，把这个新 `.lic` 交给客户替换。不要只改数据库而不把文件交给客户。

**改到期日或机器指纹**

在「编辑许可」里改生效时间、到期时间或机器指纹，点「确定」后，发版台按原 `license_id` 重新签名。客户机器上已有的 `.lic` 不会跟着变，里面仍是改之前的到期日和指纹。必须按第 8 节把新文件交下去并重启 worker。只在网页上保存，客户现场不会生效。

**停用**

编辑里把状态改为停用。已绑定的许可下一次领租约会被拒绝。未绑定的许可发版台这边记为停用，已经在客户机器上的文件仍只看本地到期日，不能远程立刻作废。

---

## 7. 许可文件每个字段

客户机器上的文件形如 `licenses/<许可ID>.lic`，是 JSON。

| 字段 | 作用 |
| --- | --- |
| `license_id` | 这张许可的编号。领租约、记时钟都用它。编辑后重签，编号不变。新建一张，编号会变。 |
| `customer` | 签发给哪家客户。在签名范围内，改了签名就失效。worker 不拿它和登录账号对比。 |
| `product` | 必须是 `spectrum-mosaic`。对不上就拒绝启动。 |
| `release_version` | 签发时选的版本号。在签名范围内。worker 不拿它和镜像标签逐字对比。 |
| `starts_at` | 生效时间，UTC。当前时间早于它，提示「许可尚未生效」。 |
| `expires_at` | 到期时间，UTC。晚于它，提示「许可无效或已过期」。 |
| `issued_at` | 最近一次签名的时间。改指纹或改有效期并保存后，这个时间会更新。 |
| `machine_id` | 绑定的机器指纹。有这个字段才核对机器，并向发版台领租约。没有这个字段就是未绑定。 |
| `signature` | 以上全部字段的 Ed25519 签名。 |

时钟记录在客户数据目录 `data/.license/clock.json`。本机时间相对上次成功校验回拨超过容差（默认 1 小时，可在「全局配置」里改）时，worker 拒绝运行。删除该文件会重置记录，所以它提高的是随意改时间的成本。

---

## 8. 交付包里如何替换许可

镜像不用重做，整包也不用重打。

1. 在「许可签发」改好指纹或有效期，保存，下载新的 `.lic`。
2. 放到客户已解压的目录，覆盖同名文件：

```text
<交付包目录>/licenses/<许可ID>.lic
```

文件名保持 `license_id`，不要改名。

3. 若这张许可已绑定机器，在客户目录的 `.env` 填写发版台根地址：

```bash
LICENSE_SERVER_URL=http://<发版台主机>:8180
```

未绑定许可这里留空。

4. 在交付包目录重启 worker。只覆盖文件时，正在跑的进程仍拿着旧许可，需要重建容器：

```bash
docker compose up -d --force-recreate worker
docker compose logs --tail=80 worker
```

Windows 在同一目录的命令提示符里执行同样两条命令。

成功时日志里有 `license ok`，`expires=` 与发版台当前到期时间一致（日志是 UTC）。已绑定的还会带 `machine=bound`。不应出现「许可无效或已过期」「许可尚未生效」「许可与当前机器不匹配」「无法连接许可服务器」。

客户执行 `./start.sh` 时会先打印本机 `machine_id`。未绑定许可看到即可。已绑定许可用它核对是不是这台机器；指纹不对要回到发版台改签，而不是在客户机器上改 `.lic` 里的字段。

---

## 9. 交付包里有什么

```text
交付包.zip
├── start.sh                 打印机器指纹，必要时启用 GPU，导入镜像，生成 .env，启动
├── docker-compose.yml       不挂算法源码
├── docker-compose.gpu.yml   NVIDIA 覆盖层；没卡时不要单独使用
├── .env.example
├── README.md
├── licenses/<许可ID>.lic
├── docs/拼图服务私有部署操作手册.md
├── docs/拼图服务WEB操作手册.md
└── images/                  离线镜像（勾选全量时）
    ├── load-images.sh
    ├── worker-image.tar
    ├── backend-image.tar
    ├── frontend-image.tar
    ├── postgres-image.tar
    └── rabbitmq-image.tar
```

包内没有发版台、没有私钥、没有已编译模块对应的算法 `.py`。`docker-compose.yml` 使用 `pull_policy: never`，客户机器缺镜像会直接失败，不会去仓库拉取。

页面端口以包里 `.env` 的 `FRONTEND_PORT` 为准。现在的模板默认是 **9080**。早于这次修改打出的包仍可能是 **8080**，以该包的 `.env` 为准，改端口后执行 `docker compose up -d`。

客户侧初始账号来自安装种子数据：管理员 `admin` / `admin123`，试用 `trial1`、`trial2` / `trial@123456`。试用账号只能使用自己的数据目录。

---

## 10. 现场记录：中达瑞和 Windows 私有部署（2026-11-18）

客户是深圳市中达瑞和科技有限公司。许可 `37f671b2-df15-40f7-b96d-f37856ba5faa` 装在 Windows 电脑 `E:\spectrum-mosaic`，页面 `http://localhost:9080`。发版台是 `http://123.56.26.229:8180`。

| 项 | 值 |
| --- | --- |
| 设备名 | `CHINAMI-IPKILU3` |
| 系统 | Windows 10 专业版 22H2 x64，公司镜像，账号 `Administrator` |
| CPU / 内存 | Intel Core i5-12600KF（10 核 16 线程），64 GB |
| 显卡 | NVIDIA GeForce RTX 4070 Ti SUPER（另有向日葵虚拟显示器 `OrayIddDriver Device`，不是计算卡） |
| 安装目录 | `E:\spectrum-mosaic` |
| 机器码 | Docker 虚拟机 `/etc/machine-id`：`791af66aa8344bf8beade4efb4045f49` |
| 机器指纹 | `607d8cfb88bcb03f7680abe8d7c605d064bf1a90e726b0c67a16b91fb972aaa2` |
| 原到期 | `2027-12-31T00:00:00Z` |
| 现到期 | `2026-12-15T00:00:00Z`（页面上为北京时间 2026-12-15 08:00） |
| 租约地址 | `LICENSE_SERVER_URL=http://123.56.26.229:8180` |

这张许可原先绑的是测试机 `10.156.196.228` 的指纹 `d6a4829f0e6c6612762ec47c214e517576a8f39ffda4f7669c985095baee9649`。改绑到这台 Windows 之后，测试机下次领租约会被拒绝。

机器码在交付包目录用一行读出，不要和别的命令粘在同一行：

```bat
docker run --rm -v /etc/machine-id:/host/machine-id:ro --entrypoint cat postgres:16-alpine /host/machine-id
```

写进「机器指纹」的是这 32 位十六进制去掉换行后的 SHA-256，不是机器码本身。

2026-11-18 把到期日改到 12 月 15 日。编辑框点「确定」后必须重新下发，客户机上的旧文件不会自动更新：

1. 在「许可签发」下载新的 `37f671b2-df15-40f7-b96d-f37856ba5faa.lic`。
2. 覆盖 `E:\spectrum-mosaic\licenses\` 里的同名文件。机器指纹那一格不要改。
3. `.env` 里保持 `LICENSE_SERVER_URL=http://123.56.26.229:8180`。
4. 在 `E:\spectrum-mosaic` 双击 `start.cmd`（有 GPU 覆盖层时不要只跑不带 `-f docker-compose.gpu.yml` 的 `compose up`，否则显卡会从容器里拿掉）。只换许可时也可以：
   `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --force-recreate worker`
5. 日志应为 `license ok`，`expires=2026-12-15T00:00:00Z`，`machine=bound`。

### 让 4070 Ti SUPER 出现在「任务 GPU」

页面读的是 worker 容器里的 CuPy，不是 Windows 设备管理器。CPU 版 worker 永远列不出这张卡。要用 GPU：

1. 发版台新建 **amd64** 版本（建议 `0.1.0-rc2`，不要覆盖已发出的 rc1 产物）并构建。日志里应有 `worker dockerfile=...Dockerfile.worker-customer-gpu`。
2. 打交付包，或至少取出新的 `worker-image.tar` 和 `docker-compose.gpu.yml`。
3. 在 `E:\spectrum-mosaic`：把 `docker-compose.gpu.yml` 放到与 `docker-compose.yml` 同一层；`docker load -i worker-image.tar`（tag 须与 compose 里 worker 的 image 一致）。
4. 确认 Docker Desktop 为 WSL 2 引擎、本机 `nvidia-smi` 能列出 RTX 4070 Ti SUPER。
5. 双击 `start.cmd`。窗口应出现「检测到 NVIDIA 显卡，启用 GPU」。若出现「未能把显卡交给容器」，先重启 Docker Desktop 后再开一次，不要只用 `docker compose up -d`（那不会叠加 GPU 文件）。
6. 打开新建处理：下拉应有 `GPU 0 NVIDIA GeForce RTX 4070 Ti SUPER`。计算节点页同样列出这张卡。选「自动」即可；匹配仍走 CPU。

Docker 虚拟磁盘在 C 盘。给 WSL 的内存建议约 48 GB、处理器 12，见 `C:\Users\Administrator\.wslconfig`。改完执行 `wsl --shutdown` 并重启 Docker。
