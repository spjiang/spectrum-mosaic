#!/usr/bin/env bash
# 在本机跑空三热点回归（连接点 / 剔粗差 / 候选像对）。
# 优先 macOS 上的 worker/.venv；否则用已启动的 server-worker-1 容器。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

extra=("$@")
if ((${#extra[@]} == 0)); then
  extra=(tests/test_performance_hotpaths.py -q)
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m pytest "${extra[@]}"
fi

if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx 'server-worker-1'; then
  exec docker exec -w /app server-worker-1 python -m pytest "${extra[@]}"
fi

echo "未找到 $ROOT/.venv，也未发现 server-worker-1。" >&2
echo "本机建议：" >&2
echo "  cd \"$ROOT\" && python3 -m venv .venv && .venv/bin/pip install -r requirements-worker.txt" >&2
echo "  ./scripts/run_hotpath_tests.sh" >&2
exit 1
