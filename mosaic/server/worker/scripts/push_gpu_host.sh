#!/usr/bin/env bash
# 把 worker 代码同步到 GPU 验收服务器的 /home/spectrum-mosaic/worker，然后执行 gpu_host_check.sh 的子命令。
#   GPU_HOST=root@10.17.19.198 GPU_PASS=... bash push_gpu_host.sh probe
#   GPU_HOST=root@10.17.19.198 GPU_PASS=... bash push_gpu_host.sh test tests/test_ortho_gpu.py
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${GPU_HOST:?请设置 GPU_HOST，如 root@10.17.19.198}"
ROOT="${ROOT:-/home/spectrum-mosaic}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

if [ -n "${GPU_PASS:-}" ]; then
  command -v sshpass >/dev/null || { echo "需要 sshpass" >&2; exit 1; }
  export SSHPASS="$GPU_PASS"
  WRAP=(sshpass -e)
else
  WRAP=()
fi

"${WRAP[@]}" ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p '$ROOT/worker'"
"${WRAP[@]}" rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'runs/' --exclude '.inspect/' \
  "$HERE/ms_mosaic" "$HERE/tests" "$HERE/scripts" "$HERE/pytest.ini" \
  "$HERE/requirements-worker.txt" "$HERE/requirements-gpu.txt" \
  "$HOST:$ROOT/worker/"
"${WRAP[@]}" ssh "${SSH_OPTS[@]}" "$HOST" \
  "ROOT='$ROOT' CORES='${CORES:-0-31}' MEM_MAX='${MEM_MAX:-64G}' GPUS='${GPUS:-0}' bash '$ROOT/worker/scripts/gpu_host_check.sh' $*"
