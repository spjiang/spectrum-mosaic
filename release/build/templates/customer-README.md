# {{CUSTOMER}} · spectrum-mosaic {{VERSION}}

本包为客户交付物，不含发版台与算法源码。含离线全量镜像时，**无需访问任何镜像仓库**。

本包镜像架构为 **linux/{{ARCH}}**，须与宿主机 CPU 架构一致（`uname -m`：`x86_64`→amd64，`aarch64`/`arm64`→arm64）。

## 1. 准备

1. 安装 Docker 与 Docker Compose。
2. 确认 `licenses/{{LICENSE_FILE}}` 存在，且未过期。
3. （可选）复制并修改环境变量：`cp .env.example .env`。若不存在 `.env`，`./start.sh` 会自动生成。

## 2. 启动（推荐）

在交付包根目录执行：

Linux / macOS：

```bash
chmod +x start.sh
./start.sh
```

Windows（在交付包根目录，PowerShell）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

也可以双击 `start.cmd`。

`start.sh` / `start.ps1` 会依次：准备 `data/` 目录（供 worker 写入许可时钟）→ 必要时生成 `.env` → 仅当本机还没有 compose 所需镜像时导入 `images/` → `docker compose up -d`。本机有 NVIDIA 且 `nvidia-smi` 可用时，自动叠加 `docker-compose.gpu.yml` 把显卡交给 worker；失败则退回 CPU。关机或重启后仍用同一脚本，镜像已在时不会再次 `docker load`。有显卡的机器不要只执行不带覆盖层的 `docker compose up`，否则页面看不到卡。

也可分步手动：`./images/load-images.sh` → `cp .env.example .env` → 自行处理 `data` 权限 → `docker compose up -d`。

`docker-compose.yml` 已设置 `pull_policy: never`：本地没有对应 tag 的镜像时会直接失败，而不会去拉仓库。

页面默认 http://localhost:9080 （以 `.env` 中 `FRONTEND_PORT` 为准）。worker 启动时会校验许可；过期、签名无效、机器指纹不符或系统时间异常回拨时进程退出（详见 `docs/拼图服务私有部署操作手册.md`）。

`start.sh` 或 `start.ps1` 会打印本机 `machine_id`。绑定许可须把该值交给供应商写入 `.lic`。`LICENSE_SERVER_URL` 已写在 `.env.example`，首次启动会复制到 `.env`。未写 `machine_id` 的旧许可仍按离线方式校验。

## 3. 操作手册

- `docs/拼图服务私有部署操作手册.md`：安装、启动、目录和许可
- `docs/拼图服务WEB操作手册.md`：登录、新建处理、查看和下载成果

## 4. 注意

- 不要挂载任何拼图源码目录进容器。
- 数据库在交付包根目录的 `pgdata/`。不要删除，不要移入 `data/`。
- 卸载：Windows 双击 `uninstall.cmd`，Linux 执行 `./uninstall.sh`。只删除容器和镜像，不删除 `data/`、`pgdata/`、`licenses/` 和 `.env`。
- 续期：向供应商索取新的 `.lic`，替换 `licenses/` 下文件后重启 worker。
- 本机时间需准确；许可按 UTC 校验生效与到期时间。
