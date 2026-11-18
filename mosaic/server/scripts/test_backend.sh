#!/usr/bin/env sh
# 在 backend 容器内跑全量单测，避免本机缺 tifffile / 架构不一致。
set -eu
cd "$(dirname "$0")/.."
if ! docker compose ps backend 2>/dev/null | grep -q Up; then
  docker compose up -d postgres rabbitmq backend
fi
docker compose exec -T backend python -m pytest tests -q "$@"