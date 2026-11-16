#!/usr/bin/env sh
# 客户交付包一键启动：准备 data 权限 → 导入离线镜像 → 生成 .env → compose up
set -eu
cd "$(dirname "$0")"

# 须与 mosaic/server/backend/app/services/paths.py 的 WORKER_UID 一致
WORKER_UID=10001

if [ -r /etc/machine-id ]; then
  mid=$(tr -d '[:space:]' < /etc/machine-id | tr '[:upper:]' '[:lower:]')
  if command -v sha256sum >/dev/null 2>&1; then
    fp=$(printf '%s' "$mid" | sha256sum | awk '{print $1}')
  else
    fp=$(printf '%s' "$mid" | shasum -a 256 | awk '{print $1}')
  fi
  echo ">>> 机器指纹 machine_id=$fp"
  echo ">>> 签发绑定许可时把该指纹提供给供应商。该许可不能在其他机器运行。"
else
  echo "警告: 读不到 /etc/machine-id，无法计算机器指纹" >&2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker，请先安装 Docker。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "错误: 未找到 docker compose，请先安装 Docker Compose。" >&2
  exit 1
fi

echo ">>> 准备 data 目录与权限（worker uid=$WORKER_UID）"
mkdir -p data/.license data/.worker data/input data/output
if chown -R "${WORKER_UID}:${WORKER_UID}" data 2>/dev/null; then
  echo ">>> data 属主已设为 ${WORKER_UID}:${WORKER_UID}"
else
  chmod -R a+rwX data
  echo ">>> 无 chown 权限，已对 data 执行 chmod -R a+rwX"
fi

if [ -f images/load-images.sh ]; then
  chmod +x images/load-images.sh
  echo ">>> 导入离线镜像"
  ./images/load-images.sh
else
  echo ">>> 未找到 images/load-images.sh，跳过镜像导入（请确认本地已有所需镜像）"
fi

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo ">>> 已从 .env.example 生成 .env"
  else
    echo "错误: 缺少 .env 与 .env.example" >&2
    exit 1
  fi
fi

if [ ! -d licenses ] || [ -z "$(ls licenses/*.lic 2>/dev/null || true)" ]; then
  echo "警告: licenses/ 下未发现 .lic 文件，worker 可能无法启动" >&2
fi

echo ">>> docker compose up -d"
docker compose up -d
docker compose ps

FRONTEND_PORT=8080
line=$(grep -E '^FRONTEND_PORT=' .env 2>/dev/null | head -1 || true)
if [ -n "$line" ]; then
  val=${line#FRONTEND_PORT=}
  val=$(printf '%s' "$val" | tr -d '[:space:]' | tr -d '"' | tr -d "'")
  if [ -n "$val" ]; then
    FRONTEND_PORT=$val
  fi
fi

echo ">>> 启动完成。页面: http://localhost:${FRONTEND_PORT}/"
echo ">>> 查看日志: docker compose logs -f"
