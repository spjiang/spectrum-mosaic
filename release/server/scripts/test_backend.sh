#!/usr/bin/env sh
# 在发版台 backend 容器内跑单测（含 PyNaCl / 租约 API）。
set -eu
cd "$(dirname "$0")/.."
if ! docker compose ps backend 2>/dev/null | grep -q Up; then
  docker compose up -d postgres backend
fi
docker compose exec -T backend sh -c 'cd /workspace/release/server/backend && python -m pytest tests -q' -- "$@"