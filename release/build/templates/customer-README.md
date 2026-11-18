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

`start.sh` / `start.ps1` 会依次：准备 `data/` 目录（供 worker 写入许可时钟）→ 若存在 `images/` 则导入离线镜像 → 必要时生成 `.env` → `docker compose up -d`。

也可分步手动：`./images/load-images.sh` → `cp .env.example .env` → 自行处理 `data` 权限 → `docker compose up -d`。

`docker-compose.yml` 已设置 `pull_policy: never`：本地没有对应 tag 的镜像时会直接失败，而不会去拉仓库。

页面默认 http://localhost:9080 （以 `.env` 中 `FRONTEND_PORT` 为准）。worker 启动时会校验许可；过期、签名无效、机器指纹不符或系统时间异常回拨时进程退出（详见 `docs/操作手册`）。

`start.sh` 或 `start.ps1` 会打印本机 `machine_id`。绑定许可须把该值交给供应商写入 `.lic`，并在 `.env` 填写 `LICENSE_SERVER_URL`（发版台地址）。未写 `machine_id` 的旧许可仍按离线方式校验。

## 3. 操作手册

完整部署与业务使用说明见：

- `docs/操作手册.md`
- `docs/操作手册.pdf`

## 4. 注意

- 不要挂载任何拼图源码目录进容器。
- 续期：向供应商索取新的 `.lic`，替换 `licenses/` 下文件后重启 worker。
- 本机时间需准确；许可按 UTC 校验生效与到期时间。
