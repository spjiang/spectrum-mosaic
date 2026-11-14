#!/usr/bin/env bash
# 在 198 上用真实测区跑 online_sparse_trial.py，轮询至 ---JSON--- 并保存到本机。
#   GPU_HOST=root@10.17.19.198 GPU_PASS=... bash scripts/run_online_sparse_trial_remote.sh
#   GPU_HOST=... GPU_PASS=... bash scripts/run_online_sparse_trial_remote.sh poll   # 只拉日志
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${GPU_HOST:?请设置 GPU_HOST}"
ROOT="${ROOT:-/home/spectrum-mosaic/server}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30)
INPUT="${INPUT_DIR:-/home/spectrum-mosaic/data/S810原始数据/20260528原始数据/预处理-辐射+JPG/1530}"
MAX_FRAMES="${MAX_FRAMES:-25}"
WORKERS="${WORKERS:-32}"
MEM_GB="${MEM_GB:-64}"
CPUS="${CPUS:-32}"
LOG="${REMOTE_LOG:-/tmp/online_sparse_trial.log}"
LOCAL_OUT="${LOCAL_OUT:-$HERE/runs/online_sparse_trial_last.json}"
LOCAL_LOG="${LOCAL_LOG:-$HERE/runs/online_sparse_trial_last.log}"

if [ -n "${GPU_PASS:-}" ]; then
  command -v sshpass >/dev/null || { echo "需要 sshpass" >&2; exit 1; }
  export SSHPASS="$GPU_PASS"
  WRAP=(sshpass -e)
else
  WRAP=()
fi

remote() { "${WRAP[@]}" ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

sync_code() {
  remote "mkdir -p '$ROOT/worker'"
  "${WRAP[@]}" rsync -az -e "ssh ${SSH_OPTS[*]}" \
    --exclude '__pycache__' --exclude '.pytest_cache' \
    "$HERE/ms_mosaic" "$HERE/scripts/online_sparse_trial.py" \
    "$HOST:$ROOT/worker/"
}

start_trial() {
  sync_code
  remote "nohup python3 '$ROOT/worker/scripts/online_sparse_trial.py' \
    '$INPUT' --max-frames $MAX_FRAMES --workers $WORKERS --memory-gb $MEM_GB --cpus $CPUS \
    > '$LOG' 2>&1 & echo started_pid=\$!"
}

poll_until_done() {
  mkdir -p "$(dirname "$LOCAL_OUT")"
  for _ in $(seq 1 120); do
    if ! remote "pgrep -f online_sparse_trial.py >/dev/null" 2>/dev/null; then
      break
    fi
    sleep 30
  done
  remote "cat '$LOG'" > "$LOCAL_LOG" || true
  if grep -q '---JSON---' "$LOCAL_LOG" 2>/dev/null; then
    awk '/---JSON---/{buf=""; f=1; next} f{buf=buf $0 "\n"} END{print buf}' "$LOCAL_LOG" \
      | awk 'BEGIN{b=0} /^\{/{b=1} b{print} b && /^\}/{exit}' > "$LOCAL_OUT"
    echo "已写入 $LOCAL_OUT"
    head -20 "$LOCAL_OUT"
  else
    echo "未找到 ---JSON---，见 $LOCAL_LOG 末尾：" >&2
    tail -30 "$LOCAL_LOG" >&2
    exit 1
  fi
}

case "${1:-all}" in
  sync) sync_code ;;
  start) start_trial ;;
  poll) poll_until_done ;;
  all)
    start_trial
    poll_until_done
    ;;
  *)
    echo "用法: $0 [sync|start|poll|all]" >&2
    exit 2
    ;;
esac
