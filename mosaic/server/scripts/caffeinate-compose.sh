#!/usr/bin/env bash
# macOS：长跑拼图时防止系统睡眠导致阶段墙钟虚高、进程挂起。
# 用法：./scripts/caffeinate-compose.sh up -d worker
# 或：  ./scripts/caffeinate-compose.sh logs -f worker
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "caffeinate 仅 macOS 需要；Linux 请直接 docker compose $*" >&2
  exec docker compose -p server "$@"
fi
exec caffeinate -dims docker compose -p server "$@"
