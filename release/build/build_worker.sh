#!/usr/bin/env bash
# 在宿主机构建客户离线全量镜像栈（与发版台 build_runner 一致）。
# 用法：
#   ./release/build/build_worker.sh 0.1.0 amd64
set -euo pipefail
VERSION="${1:?用法: $0 <version> <arch>}"
ARCH="${2:?用法: $0 <version> <arch>  (amd64|arm64)}"
case "$ARCH" in
  amd64|arm64) ;;
  *) echo "arch 仅支持 amd64|arm64" >&2; exit 1 ;;
esac
PLATFORM="linux/${ARCH}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEYS="$ROOT/release/keys"
PUB="$KEYS/ed25519_public.pem"
EMBED="$ROOT/release/build/.embed/license_public.pem"
WORKER="spectrum-mosaic-worker:${VERSION}-${ARCH}"
PUSHBROOM="spectrum-mosaic-pushbroom-worker:${VERSION}-${ARCH}"
BACKEND="spectrum-mosaic-backend:${VERSION}-${ARCH}"
FRONTEND="spectrum-mosaic-frontend:${VERSION}-${ARCH}"
OUT="$ROOT/release/artifacts/releases/manual-${VERSION}-${ARCH}"
mkdir -p "$OUT" "$(dirname "$EMBED")"
if [[ ! -f "$PUB" ]]; then
  echo "缺少公钥 $PUB：请先启动发版台以生成密钥，或手工 init_keys" >&2
  exit 1
fi
cp "$PUB" "$EMBED"

if [[ "$ARCH" == "amd64" ]]; then
  WORKER_DF="$ROOT/release/build/Dockerfile.worker-customer-gpu"
else
  WORKER_DF="$ROOT/release/build/Dockerfile.worker-customer"
fi
echo "=== build worker ($PLATFORM) df=$(basename "$WORKER_DF") ==="
docker build --platform "$PLATFORM" -f "$WORKER_DF" -t "$WORKER" "$ROOT" | tee "$OUT/build.log"
docker save -o "$OUT/worker-image.tar" "$WORKER"

echo "=== build pushbroom worker ($PLATFORM) ==="
docker build --platform "$PLATFORM" -f "$ROOT/release/build/Dockerfile.pushbroom-customer" -t "$PUSHBROOM" "$ROOT" | tee -a "$OUT/build.log"
docker save -o "$OUT/pushbroom-worker-image.tar" "$PUSHBROOM"

echo "=== build backend ($PLATFORM) ==="
docker build --platform "$PLATFORM" -f "$ROOT/release/build/Dockerfile.backend-customer" -t "$BACKEND" "$ROOT" | tee -a "$OUT/build.log"
docker save -o "$OUT/backend-image.tar" "$BACKEND"

echo "=== build frontend ($PLATFORM) ==="
docker build --platform "$PLATFORM" -t "$FRONTEND" "$ROOT/mosaic/server/frontend" | tee -a "$OUT/build.log"
docker save -o "$OUT/frontend-image.tar" "$FRONTEND"

echo "=== save deps ($PLATFORM) ==="
docker pull --platform "$PLATFORM" postgres:16-alpine
docker pull --platform "$PLATFORM" rabbitmq:3.13-management-alpine
docker save -o "$OUT/postgres-image.tar" postgres:16-alpine
docker save -o "$OUT/rabbitmq-image.tar" rabbitmq:3.13-management-alpine

cat > "$OUT/images.manifest" <<EOF
arch=$ARCH
platform=$PLATFORM
worker=$WORKER
pushbroom_worker=$PUSHBROOM
backend=$BACKEND
frontend=$FRONTEND
dep=postgres:16-alpine
dep=rabbitmq:3.13-management-alpine
EOF

echo "OK full stack -> $OUT"
ls -lh "$OUT"/*.tar
