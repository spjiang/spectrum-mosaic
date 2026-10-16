#!/usr/bin/env bash
# 按 docker-compose.yml 起全套：Postgres + RabbitMQ + backend + frontend + worker
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.run"
cd "$ROOT"

echo "stopping host uvicorn/vite (ports 8000/8080)..."
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -f 'server/frontend/node_modules/.bin/vite' 2>/dev/null || true
for port in 8000 8080; do
  for i in $(seq 1 25); do
    if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      break
    fi
    if [[ "$i" -eq 20 ]]; then
      kill $(lsof -t -iTCP:"$port" -sTCP:LISTEN) 2>/dev/null || true
    fi
    sleep 0.2
  done
done

# 清掉已删目录 visual_server 留下的旧容器，避免抢 5432/5672/8000/8080
old=$(docker ps -aq --filter 'name=visual_server-' 2>/dev/null || true)
if [[ -n "${old}" ]]; then
  echo "removing leftover visual_server containers..."
  docker stop $old >/dev/null 2>&1 || true
  docker rm $old >/dev/null 2>&1 || true
fi

echo "docker compose up (postgres rabbitmq backend frontend worker)..."
docker compose up -d --build --wait

echo "frontend: http://127.0.0.1:8080/"
echo "backend:  http://127.0.0.1:8000/api/health"
curl -sS http://127.0.0.1:8000/api/health || true
echo
curl -sS -o /dev/null -w "fe_http=%{http_code}\n" http://127.0.0.1:8080/ || true
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAMES|^server-' || true
echo "login: admin / admin123"
