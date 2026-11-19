# 发版台内部操作手册

本文只给发版人员使用，在发版台「手册 → 内部手册」阅读。打交付包时**不会**附带本文。给客户的是「手册 → 操作手册」里的两份拼图服务手册。

当前发版台只有一台：`http://123.56.26.229:8180`。登录后改过的管理员密码以现场为准。私钥和许可数据库都在这台机器上，迁走时要一起带走。

---

## 1. 一次发版实际怎么做

按这个顺序。后面几节解释每一步为什么这样。

**① 全局配置**

打开「系统配置 → 全局配置」，确认：

| 项 | 现在的值 | 作用 |
| --- | --- | --- |
| 许可服务器地址 | `http://123.56.26.229:8180` | 打进交付包的 `LICENSE_SERVER_URL`。不要带 `/api/...` |
| 租约有效期 | 72 小时 | 绑定许可每次领到的租约能离线用多久。下一次领取就按新值签，不用重打镜像 |
| 租约领取间隔 | 6 小时 | 写进客户 `docker-compose.yml`。worker 启动时领一次，之后按这个间隔再领 |
| 回拨容差 | 1 小时 | 客户机时间相对上次成功校验回拨超过这个值，worker 拒绝运行 |

某一家要单独时长，在「客户档案」里填租约有效期、领取间隔、回拨容差。留空就用上面的全局值。改全局或客户档案里的租约有效期，**不用**重打已发出的包，客户下一次领租约即生效。改领取间隔或回拨容差，要重打交付包，或到客户机改 `docker-compose.yml` 里对应环境变量后重建 worker。

**② 版本构建**

登记版本号，并选 CPU 架构。`amd64` 和 `arm64` 各构建一次，包不能混用。

现在发给 Windows 带 NVIDIA 显卡的客户，选 **amd64**。这个架构的 worker 按 GPU 镜像构建（CUDA 12.2 + CuPy）。`arm64` 仍是 CPU 镜像。构建日志里应能看到 `Dockerfile.worker-customer-gpu`。状态变成成功后才能打交付包。失败在本页看日志。

**③ 客户档案**

单位名称会写进许可和 zip 文件名。状态为停用的客户不能新签发。

**④ 许可签发**

点「签发许可」，选客户、版本、生效时间、到期时间。

机器指纹**可以留空**。留空是未绑定：只验签名、生效日、到期日和时钟，不核对机器，也不向发版台领租约。先这样打出交付包，让客户把机器跑起来。

客户把指纹发回后，在同一条许可上点「编辑」，填入 64 位十六进制，保存。`license_id` 不变，文件会重新签名。再点该行的「下载」，把新的 `.lic` 交给客户覆盖。不要只在网页上保存。

已知指纹时，签发时就可以直接填上。

**⑤ 交付包**

点「生成交付包」。选择客户、构建成功的版本、一条有效许可。默认勾选带上离线镜像。

绑定了机器、但全局配置里许可服务器地址是空的，这一步会失败。未绑定许可可以在地址为空时打包。

生成后的 zip 是**那一刻**许可文件和全局配置的副本。之后在「许可签发」里改指纹或到期日，这个 zip **不会**变。列表上的校验和是 zip 的 SHA-256，下载后在本机再算一次，应与这一列完全一致。

- 「查看内容」列出包里的路径，用来确认有没有 `licenses/`、镜像和两份客户手册。
- 「下载」交给浏览器自己的下载栏。包大约几百 MB，点下去页面会提示「开始下载」，进度不在表格里。

**⑥ 客户机器上拿到指纹**

Linux 解压后执行 `./start.sh`，窗口第一行就是 `machine_id=`。

Windows 解压后双击 `start.cmd`。它调用 `start.ps1`，同样先打印 `machine_id=`。这是 Docker 虚拟机里 `/etc/machine-id` 的 SHA-256，不是 Windows 的设备名，也不是那一串 32 位机器码本身。只要单独读机器码，在交付包目录执行：

```bat
docker run --rm -v /etc/machine-id:/host/machine-id:ro --entrypoint cat postgres:16-alpine /host/machine-id
```

把读到的内容去掉换行再做 SHA-256，才是填进发版台的指纹。

**⑦ 绑上机器并替换许可**

1. 发版台编辑该许可，填入指纹，保存，下载 `.lic`。
2. 覆盖客户已解压目录里的同名文件：`licenses/<许可ID>.lic`。文件名不要改。
3. 客户目录 `.env` 里 `LICENSE_SERVER_URL=http://123.56.26.229:8180`。未绑定许可这里留空。打包含时如果全局配置已经填了地址，`.env.example` 里会带上，复制成 `.env` 即可。
4. 只覆盖文件时，正在跑的 worker 仍拿着旧许可。重建 worker：

```bash
docker compose up -d --force-recreate worker
docker compose logs --tail=80 worker
```

Windows 有显卡时不要漏掉 GPU 覆盖层，否则显卡会从容器里拿掉：

```bat
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --force-recreate worker
```

或者直接再双击一次 `start.cmd`。

成功时日志有 `license ok`。`expires=` 是 UTC，应与发版台当前到期时间一致。已绑定还会带 `machine=bound`。

**⑧ 到租约日志核对**

「发版管理 → 租约日志」按时间倒序分页。绑定许可每次来领，成功或被拒绝都有一行：客户、许可、机器指纹、说明、租约到期、来源地址。点客户名称或许可编号，打开对应档案。

来领成功说明三件事同时成立：`.lic` 已换成新文件、指纹与这台机器一致、客户机能访问 `http://123.56.26.229:8180`。

---

## 2. 两套系统

| | 发版台 | 客户交付包 |
| --- | --- | --- |
| 谁用 | 我方 | 客户现场 |
| 跑什么 | 页面、发版接口、PostgreSQL | 拼图页面、任务接口、worker、数据库、消息队列 |
| 有没有算法源码 | 有，用来编译客户镜像 | 没有对应的 `.py` |
| 有没有签发私钥 | 有，只在发版台 | 没有。镜像里只有验签公钥 |
| 地址 | `http://123.56.26.229:8180` | 包内 `.env` 的 `FRONTEND_PORT`，模板默认 **9080** |

客户机器归客户管。目录权限挡不住拷贝，所以不把算法明文和私钥交出去。

发版台页面端口是 **8180**。后端 8100 和数据库 5433 只监听该机器本机，不对公网开放。页面的 nginx 把 `/api/` 转到后端。领租约的地址是：

```text
POST http://123.56.26.229:8180/api/license/check
```

`LICENSE_SERVER_URL` 只填到 `:8180`，worker 自己接上 `/api/license/check`。这个接口不需要登录。

---

## 3. 保护原理

这里不是「把源码加密，再在客户机器上解密」。程序必须在客户机器上运行，解密密钥如果也在那台机器上，对方可以一起拷走。

**1. 算法不交付明文**

构建客户 worker 镜像时，把 `ms_mosaic` 里列出的算法模块用 Cython 编译成 `.so`，并删除对应的 `.py`。清单在 `release/build/cython_modules.txt`。构建结束会检查 `pipeline.py` 不在镜像里。

启动入口 `worker_main.py` 和验签程序 `license_check.py` 仍是 Python。页面和任务接口也不做这层编译。

**2. 没有有效许可就不能算**

许可是 JSON 加 Ed25519 签名。改任何一个字段，签名对不上，worker 拒绝启动。客户镜像设置 `MOSAIC_REQUIRE_LICENSE=1`。开发环境默认不强制验许可。

能挡住的是：直接打开文件抄算法、自己改到期日、把已绑定的许可拷到另一台机器。挡不住的是：客户有 root，且会逆向机器码或改验签入口。

---

## 4. 公钥和私钥

密钥在发版台的 `release/keys/`，不进 git，不进客户包，不进客户镜像。

| 文件 | 谁拿着 | 用来做什么 |
| --- | --- | --- |
| `ed25519_private.pem` | 只在发版台 | 签发 `.lic`；客户来领租约时再签一份短时租约 |
| `ed25519_public.pem` | 构建客户镜像时打进 worker | 客户机器上验 `.lic` 和租约 |

第一次签发时如果目录里还没有密钥，发版台会自动生成一对。换私钥后，以前发出的许可和镜像里的公钥对不上，旧许可全部失效。同步代码到发版台时不要覆盖或删除 `release/keys/`。

租约用同一对密钥。有效期取「全局或该客户的租约时长」和许可到期日里更早的那个。

---

## 5. 许可文件每个字段

客户机器上的文件是 `licenses/<许可ID>.lic`。

| 字段 | 作用 |
| --- | --- |
| `license_id` | 这张许可的编号。领租约、记时钟都用它。编辑后重签，编号不变。新建一张，编号会变，客户机上的文件名也要换成新的 |
| `customer` | 签发给哪家客户。在签名范围内。worker 不拿它和登录账号对比 |
| `product` | 必须是 `spectrum-mosaic` |
| `release_version` | 签发时选的版本号。在签名范围内。worker 不拿它和镜像标签逐字对比 |
| `starts_at` | 生效时间，UTC。当前时间早于它，提示「许可尚未生效」 |
| `expires_at` | 到期时间，UTC。晚于它，提示「许可无效或已过期」。发版台页面把这个时间显示成本地时区 |
| `issued_at` | 最近一次签名的时间。改指纹或改有效期并保存后会更新 |
| `machine_id` | 有这个字段才核对机器，并向发版台领租约。没有就是未绑定 |
| `signature` | 以上全部字段的 Ed25519 签名 |

时钟记录在客户数据目录 `data/.license/clock.json`。删除该文件会重置回拨记录。

已绑定许可的运行规则：

- 发版台拒绝（已停用、机器不对、未生效、已过期）：worker 立刻退出，并清掉本机租约。
- 暂时连不上发版台：本机还有未过期租约就可以继续算；没有租约则不能启动。
- 未绑定许可发版台记为停用后，已经在客户机器上的文件仍只看本地到期日，不能远程立刻作废。

---

## 6. 交付包里有什么

```text
交付包.zip
├── start.sh / start.ps1 / start.cmd
├── uninstall.sh / uninstall.ps1 / uninstall.cmd
├── docker-compose.yml           不挂算法源码，也不写 gpus
├── docker-compose.gpu.yml       NVIDIA 覆盖层；没卡时不要单独使用
├── .env.example                 含打包当时的 LICENSE_SERVER_URL
├── README.md
├── licenses/<许可ID>.lic
├── docs/拼图服务私有部署操作手册.md
├── docs/拼图服务WEB操作手册.md
└── images/                      勾选全量时才有
    ├── load-images.sh
    ├── worker-image.tar
    ├── backend-image.tar
    ├── frontend-image.tar
    ├── postgres-image.tar
    └── rabbitmq-image.tar
```

`docker-compose.yml` 使用 `pull_policy: never`。客户机器缺镜像会直接失败，不会去仓库拉取。不要把 `gpus` 写进默认 compose，否则没卡的机器 `compose up` 会失败。`start.ps1` 发现 NVIDIA 且 Docker 能传 GPU 时，自动叠上 `docker-compose.gpu.yml`；没有卡或传失败则走 CPU，服务仍能起来。

页面端口以包里 `.env` 的 `FRONTEND_PORT` 为准，模板默认 **9080**。

客户侧初始账号来自安装种子数据：管理员 `admin` / `admin123`，试用 `trial1`、`trial2` / `trial@123456`。试用账号只能使用自己的数据目录。

---

## 7. 现场记录：中达瑞和 Windows（2026-11-18）

客户是深圳市中达瑞和科技有限公司。许可 `37f671b2-df15-40f7-b96d-f37856ba5faa` 装在 Windows 电脑 `E:\spectrum-mosaic`，页面 `http://localhost:9080`。

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

2026-11-18 把到期日改到 12 月 15 日。编辑框点「确定」后必须重新下发：

1. 在「许可签发」下载新的 `37f671b2-df15-40f7-b96d-f37856ba5faa.lic`。
2. 覆盖 `E:\spectrum-mosaic\licenses\` 里的同名文件。机器指纹那一格不要改。
3. `.env` 里保持 `LICENSE_SERVER_URL=http://123.56.26.229:8180`。
4. 在 `E:\spectrum-mosaic` 双击 `start.cmd`。只换许可时也可以：
   `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --force-recreate worker`
5. 日志应为 `license ok`，`expires=2026-12-15T00:00:00Z`，`machine=bound`。发版台「租约日志」应出现该指纹的成功记录。

页面上的「任务 GPU」读的是 worker 容器里的 CuPy，不是 Windows 设备管理器。CPU 版 worker 列不出 4070 Ti SUPER。要用 GPU：发版台用 amd64 的 GPU 镜像构建，把新的 `worker-image.tar` 和 `docker-compose.gpu.yml` 放到 `E:\spectrum-mosaic`，`docker load` 后双击 `start.cmd`。窗口应出现「检测到 NVIDIA 显卡，启用 GPU」。Docker Desktop 须为 WSL 2 引擎，本机 `nvidia-smi` 能列出这张卡。

Docker 虚拟磁盘在 C 盘。给 WSL 的内存建议约 48 GB、处理器 12，见 `C:\Users\Administrator\.wslconfig`。改完执行 `wsl --shutdown` 并重启 Docker。
