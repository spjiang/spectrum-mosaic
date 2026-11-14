#!/usr/bin/env bash
# 本机调试 Worker。正式默认用 prod/server compose 里的 worker 容器，不要两边一起开。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../local_defaults.sh
source "$ROOT/local_defaults.sh"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export RABBITMQ_URL="${RABBITMQ_URL:-amqp://mosaic:mosaic_secret@127.0.0.1:5672/}"
cd "$ROOT"
exec "$MS_MOSAIC_PYTHON" -m ms_mosaic.worker_main
