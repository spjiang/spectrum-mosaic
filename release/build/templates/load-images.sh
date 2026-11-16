#!/usr/bin/env sh
# 离线导入交付包内全部镜像（须保留 tag，勿仅 load 匿名层）。
# 若存在 *.tar.arch 侧车文件，load 后会核对 Architecture，防止错架构包上线。
set -eu
cd "$(dirname "$0")"

required="
worker-image.tar
backend-image.tar
frontend-image.tar
postgres-image.tar
rabbitmq-image.tar
"

missing=0
for f in $required; do
  if [ ! -f "$f" ]; then
    echo "缺少 $f" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  exit 1
fi

for f in $required; do
  echo ">>> docker load -i $f"
  docker load -i "$f"
  if [ -f "${f}.arch" ]; then
    want=$(tr -d '[:space:]' < "${f}.arch")
    # 从 docker load 输出无法稳定拿 tag；按侧车期望的常见仓库名核对
    case "$f" in
      worker-image.tar)   ref=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^spectrum-mosaic-worker:' | head -1) ;;
      backend-image.tar)  ref=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^spectrum-mosaic-backend:' | head -1) ;;
      frontend-image.tar) ref=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^spectrum-mosaic-frontend:' | head -1) ;;
      postgres-image.tar) ref=postgres:16-alpine ;;
      rabbitmq-image.tar) ref=rabbitmq:3.13-management-alpine ;;
      *) ref= ;;
    esac
    if [ -n "$ref" ]; then
      got=$(docker image inspect --format '{{.Architecture}}' "$ref")
      echo ">>> arch check $ref got=$got want=$want"
      if [ "$got" != "$want" ]; then
        echo "错误: $f 加载后架构为 $got，期望 $want。请换正确架构的交付包。" >&2
        exit 1
      fi
    fi
  fi
done

echo ">>> 已加载镜像（节选）："
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}' \
  | grep -E 'REPOSITORY|spectrum-mosaic|postgres|rabbitmq' || true
echo "完成。若从包根目录执行 ./start.sh，将继续准备权限并 compose up；也可手动: docker compose up -d"
