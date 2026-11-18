#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if ! docker compose ps worker 2>/dev/null | grep -q Up; then
  docker compose up -d worker
fi
docker compose exec -T worker python -m pytest tests -q "$@"