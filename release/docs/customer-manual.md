# 高光谱拼图 · 客户操作手册

本文面向现场部署与日常使用人员。交付包不含发版台与算法源码。

---

## 1. 交付物说明

典型交付目录结构：

```text
├── docker-compose.yml
├── .env.example
├── README.md                 # 快速启动摘要
├── licenses/
│   └── <许可ID>.lic
├── docs/
│   ├── 操作手册.md
│   └── 操作手册.pdf
└── images/                   # 离线全量（推荐勾选）
    ├── load-images.sh
    ├── worker-image.tar
    ├── backend-image.tar
    ├── frontend-image.tar
    ├── postgres-image.tar
    └── rabbitmq-image.tar
```

| 内容 | 说明 |
|------|------|
| Docker Compose | 一键启动 Web 与计算 worker（`pull_policy: never`） |
| 许可文件 `.lic` | 离线 Ed25519 签名，含生效/到期时间 |
| 操作手册 | 本文（Markdown / PDF） |
| 镜像 tar（全量） | worker/backend/frontend + postgres/rabbitmq；用 `images/load-images.sh` 一次导入 |

### 1.1 推荐流程节点

```text
收包 → 【校验和核对】 → 环境准备 → 启动服务 → 登录使用 → （到期）续期替换许可
              ↑
         本节点必做：确认 zip 完整、未被篡改后再解压部署
```

| 节点 | 说明 |
|------|------|
| 收包 | 从供应商取得交付 zip |
| **校验和核对** | 用 SHA-256 与供应商提供的校验和比对（见第 2 节） |
| 环境准备 | Docker、时间同步、加载镜像、配置 `.env` |
| 启动服务 | `docker compose up -d` |
| 登录使用 | 按第 6 节业务操作 |
| 续期 | 替换 `.lic` 并重启 worker |
| 附录测试案例 | 按第 10 节在测试机走通一遍完整部署 |

---

## 2. 收包与校验和核对

> **标注：校验和 = 交付包 zip 的 SHA-256 指纹。**  
> 作用：确认下载/拷贝后的文件与供应商发出的包一致，防止传输出错或被篡改。  
> 它不是加密，也不代替许可验签；许可合法性仍由 `.lic` 签名保证。

### 2.1 怎么做

1. 向供应商索取该交付包对应的 **校验和**（发版台「交付包」列表「校验和」列，可复制完整哈希）。
2. 对本机收到的 zip **再算一次** SHA-256：

**macOS / Linux：**

```bash
shasum -a 256 交付包.zip
# 或
sha256sum 交付包.zip
```

**Windows（PowerShell）：**

```powershell
Get-FileHash .\交付包.zip -Algorithm SHA256
```

3. 将输出的一长串十六进制与供应商给出的校验和 **逐字对比**（一般不区分大小写）。
   - **一致**：可解压并进入后续部署。
   - **不一致**：停止使用该文件，重新向供应商下载；切勿带着坏包继续安装。

### 2.2 注意

- 必须对 **整个 zip 文件** 计算，不要只对解压后的某个子文件算。
- 校验通过后再解压；若先解压再怀疑损坏，应重新下载完整 zip 再核一次。

---

## 3. 环境准备

1. 安装 **Docker** 与 **Docker Compose**（Linux / Windows / macOS 均可）。
2. 机器时间需准确（建议启用 NTP）。许可按 **UTC** 校验生效与到期时间。
3. 确认宿主机 CPU 架构与交付包一致（包内 README / 文件名含 `amd64` 或 `arm64`）。`uname -m` 为 `x86_64` 对应 amd64，`aarch64`/`arm64` 对应 arm64。
4. 确认 `licenses/` 下存在对应的 `.lic` 文件，且当前时间落在许可的生效～到期窗口内。
5. （可选）复制环境变量并按需修改：`cp .env.example .env`。若不存在 `.env`，下面的 `./start.sh` 会自动生成。

---

## 4. 启动与停止

在交付包根目录执行（推荐一键脚本）：

Linux / macOS：

```bash
chmod +x start.sh
./start.sh
```

Windows 在交付包根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

或双击 `start.cmd`。

`start.sh` / `start.ps1` 会：准备 `data/`（worker 需写入 `data/.license`）→ 若有 `images/` 则导入离线全量镜像 → 必要时生成 `.env` → `docker compose up -d`。

也可仅手动启动（需已导入镜像并自行保证 `data` 可写）：

```bash
docker compose up -d
```

默认 Web 地址：`http://localhost:9080`（以 `.env` / compose 端口为准）。

查看状态与日志：

```bash
docker compose ps
docker compose logs -f
```

停止：

```bash
docker compose down
```

worker 启动时会校验许可。若签名无效、尚未生效或已过期，进程会退出，日志中可见相关提示（如「许可尚未生效」「许可无效或已过期」）。

---

## 5. 许可与续期

### 5.1 校验规则（客户侧）

- 验签公钥打在客户镜像内。
- 校验：签名有效 ∧ 产品名匹配 ∧ `生效时间 ≤ 当前时间 ≤ 到期时间`。
- 许可若带 `machine_id`：还须与宿主机 `/etc/machine-id` 的指纹一致，并向 `LICENSE_SERVER_URL` 领取 72 小时租约。断网时未过期租约仍可运行；服务器明确拒绝（停用、换机、过期）则立即退出。
- 没有 `machine_id` 的旧许可不联网，只做本地验签。
- 本机系统时间不准会导致误判，请先校时。

机器指纹在交付包根目录执行 `./start.sh`（Windows 为 `start.ps1`）时打印。换机须向供应商重新签发。

### 5.2 续期步骤

1. 向供应商索取新的 `.lic` 文件。
2. 替换 `licenses/` 目录下旧文件（或按 compose 中挂载路径更新）。
3. 重启 worker：

```bash
docker compose restart
```

或：

```bash
docker compose up -d --force-recreate
```

4. 用浏览器打开页面，确认可正常登录与提交任务。

### 5.3 时钟与回拨检测

- 本机时间须准确（建议 NTP）。许可按 **UTC** 校验生效与到期。
- worker 会在数据目录记录最近一次成功校验时间（默认 `data/.license/clock.json`）。
- 若系统时间相对该记录**回拨超过容差**（由交付包环境变量 `LICENSE_CLOCK_TOLERANCE_HOURS` 指定，常见为 1 小时），worker 拒绝运行，日志为「系统时间异常，疑似时钟回拨」。
- **请勿删除或篡改**该时钟文件；删除后虽可重置记录，但属于违规操作，续期与支持可能受影响。
- 更换新的 `.lic`（新的许可 ID）后，会按新许可重新建立记录。

### 5.4 注意

- 不要改动 `.lic` 内容；任何篡改都会导致验签失败。
- 不要把拼图**源码目录**挂进客户容器。
- 停用状态的许可由供应商侧控制；现场以文件替换与重启为准。

---

## 6. 业务使用

### 6.1 登录

1. 打开 Web 地址，使用供应商提供的账号登录。
2. 若提示试用到期或账号停用，联系供应商处理，不要自行改库。

### 6.2 新建处理

1. 进入「新建处理」或等价入口。
2. 按界面填写：
   - **标题**（可选）：便于在处理记录中辨认；留空则常用任务号+时间。
   - **输入 / 输出路径**：遵循界面提示的数据目录约定（通常在挂载的 `/data` 下）。
   - **处理方案 / 参数**：选用供应商配置好的方案，或按培训要求调整。
   - **资源**：任务内存、CPU、GPU（若节点有显卡）。无可用 GPU 时自动走 CPU，结果一致，仅耗时可能更长。
3. 确认无冲突任务占用后，点击开始处理。
4. 在执行页查看进度与处理日志。

### 6.3 处理记录

1. 在「处理记录」查看历史与进行中的任务。
2. 可打开详情：阶段进度、日志、参数快照等。
3. 按权限可暂停、继续或按界面提供的运维操作处理异常任务。
4. 需要复用参数时，可按界面提供的方式导出/下载参数，再在方案中导入（以实际菜单为准）。

### 6.4 数据与成果

- 输入数据放到约定数据卷目录，避免写到容器可写层导致重启丢失。
- 输出目录一般按用户/时间分子目录，避免互相覆盖；具体路径以页面帮助文案为准。
- 清理任务记录通常**不会**删除已生成的成果文件，删除前请确认。

### 6.5 GPU 使用简要说明

- 用交付包里的 `start.sh` / `start.cmd` 启动。本机有 NVIDIA 且 Docker 能把显卡交给容器时，「任务 GPU」会列出卡名；否则只有「自动 / 不用 GPU」，任务走 CPU。
- 可选「不用 GPU」或指定某张卡。
- 部分阶段（如匹配）可能固定走 CPU；选了 GPU 不代表每一步都用显卡。
- 显存不足时系统可能自动分批或回退 CPU，以日志为准。

---

## 7. 常见问题

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 校验和与供应商不一致 | 下载不完整、传错文件或被改 | 重新下载完整 zip 后再算 SHA-256；仍不符则联系供应商 |
| 页面打不开 | 容器未启动 / 端口未映射 | `docker compose ps`、检查防火墙与端口 |
| worker 反复退出 | 许可无效、未生效、过期或文件路径不对 | 查日志；核对 `.lic`、时间、挂载路径 |
| 提示尚未生效 | 未到许可 `starts_at` | 等待生效或联系供应商改签发时间 |
| 提示已过期 | 超过 `expires_at` | 续期替换 `.lic` 后重启 |
| 提示系统时间异常 / 疑似时钟回拨 | 本机时间被回拨超过容差（`LICENSE_CLOCK_TOLERANCE_HOURS`） | 恢复正确时间（启用 NTP）后重启；勿删 `data/.license/clock.json` |
| 任务一直排队 | 已有任务占用或资源不足 | 查看处理记录与节点资源 |
| 结果与预期不符 | 输入路径/方案参数有误 | 核对策参数与输入数据，查看任务日志 |

---

## 8. 安全与合规

- 许可文件、账号口令按保密资料保管，勿提交到公共代码库。
- 仅在授权机器上部署；镜像与 compose 按交付范围使用。
- 需要扩容、迁移或改端口时，保留原 `.env` 与 `licenses/` 备份后再改。

---

## 9. 支持

部署或使用中遇到问题，请向供应商提供：

1. 产品版本号（见交付包或页面版本信息）
2. 许可 ID（文件名或许可内容中的 ID）
3. `docker compose logs` 相关片段（可打码敏感路径）
4. 问题发生时间（建议同时注明本机时区）

---

## 10. 附录：部署测试案例

本节给出一条**可照着做的完整测试路径**，用于验收「收包 → 校验 → 启动 → 登录」。  
实际主机、账号、口令以供应商当场提供的测试信息为准（**勿把口令写进公开文档或截图外传**）。

### 10.1 测试环境约定（示例）

| 项 | 示例值 | 说明 |
|----|--------|------|
| 测试机 | `10.156.196.228` | 内网 Linux；需已装 Docker / Compose |
| SSH | `ssh root@<测试机IP>` | 口令或密钥由供应商单独告知 |
| 部署目录 | `/root/mosaic` | 交付 zip 与解压目录放此处 |
| Web 访问 | `http://<测试机IP>:9080` | 若改过 `.env` 中端口，以实际为准 |

### 10.2 操作步骤

**① 登录测试机**

```bash
ssh root@<测试机IP>
```

**② 准备目录并放入交付包**

将供应商提供的交付 zip 上传到 `/root/mosaic/`（可用 `scp` 等）。例如：

```bash
mkdir -p /root/mosaic
# 本机执行示例：
# scp 交付包.zip root@<测试机IP>:/root/mosaic/
cd /root/mosaic
ls -lh *.zip
```

**③ 校验和核对（必做）**

向供应商索取该包对应的 SHA-256（发版台「交付包」列表「校验和」列），在测试机执行：

```bash
cd /root/mosaic
sha256sum *.zip
```

输出须与供应商给出的校验和**完全一致**后再继续；不一致则重新传包，勿解压部署。

**④ 解压**

```bash
cd /root/mosaic
mkdir -p pack
unzip -o '*.zip' -d pack
cd pack
ls -la
```

期望看到：`start.sh`、`start.ps1`、`start.cmd`、`docker-compose.yml`、`.env.example`、`README.md`、`licenses/`、`docs/`；若勾选了离线全量镜像，还应有 `images/` 下五个 `*-image.tar` 与 `load-images.sh`。

**⑤ 一键启动（导入镜像 + 权限 + compose）**

```bash
chmod +x start.sh
./start.sh
docker compose ps
docker compose logs --tail=100 worker
docker images | grep -E 'spectrum-mosaic|postgres|rabbitmq'
```

Windows 在解压目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1`，或双击 `start.cmd`。

应能看到带版本 tag 的 `spectrum-mosaic-worker` / `backend` / `frontend`，以及 `postgres:16-alpine`、`rabbitmq:3.13-management-alpine`。**不应**再执行 `docker compose pull`。

compose 已配置 `pull_policy: never`：若本地缺镜像会立刻报错，而不会去公网/私有仓库拉取。首次可先用默认 `.env`；若需改端口/口令，编辑 `.env` 后 `docker compose up -d`。

worker 日志不应出现「许可无效或已过期」「许可尚未生效」「系统时间异常，疑似时钟回拨」等致命错误。

**⑥ 浏览器验收**

1. 打开 `http://<测试机IP>:9080`（或 `.env` 中的前端端口）。  
2. 使用供应商提供的账号登录。  
3. 进入「新建处理」提交一条小任务，或至少打开「处理记录」确认页面可用。  
4. 需要时查阅 `docs/操作手册.pdf`。

**⑦ 测完停止（可选）**

```bash
cd /root/mosaic/pack
docker compose down
```

### 10.3 本案例验收清单

- [ ] SHA-256 与供应商一致  
- [ ] `docker compose ps` 各服务为 running / healthy  
- [ ] Web 可打开并登录  
- [ ] worker 无因许可/时钟异常反复退出  
- [ ] （可选）能提交并看到任务进度或日志  

### 10.4 与正文其它章节的对应

| 本案例步骤 | 手册章节 |
|------------|----------|
| 校验和 | 第 2 节 |
| 环境 / 启动 | 第 3、4 节 |
| 许可与时钟 | 第 5 节 |
| 登录与业务 | 第 6 节 |
| 异常排查 | 第 7 节 |

---

*文档版本与交付包版本对应；若手册与现场界面文案略有差异，以实际界面为准。*
