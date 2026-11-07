#!/usr/bin/env bash
# 本机 MAX 全量 → 198 线上（与页面 /data/input/MAX_20251017/... 一致）
#
#   GPU_HOST=root@10.17.19.198 GPU_PASS='…' bash scripts/upload_max_full_to_198.sh
#   GPU_HOST=… GPU_PASS=… bash scripts/upload_max_full_to_198.sh check   # 只看远端空间与是否已有数据
#
# 本机源目录默认：prod/server/data/input/MAX_20251017/（含 MAX_20251017_001、可选 拼图结果）
# 远端主存：/home/spectrum-mosaic/data/MAX_20251017/
# 宿主机 Worker（server/data 挂成 /data）通过符号链接指向同一目录，避免重复占 30G。
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC_ROOT="${SRC_ROOT:-$HERE/../data/input/MAX_20251017}"
SRC_FRAMES="${SRC_FRAMES:-$SRC_ROOT/MAX_20251017_001}"
SRC_BENCH="${SRC_BENCH:-$SRC_ROOT/拼图结果}"
HOST="${GPU_HOST:?请设置 GPU_HOST，如 root@10.17.19.198}"
ROOT="${ROOT:-/home/spectrum-mosaic}"
REMOTE_ROOT="${REMOTE_ROOT:-$ROOT/data/MAX_20251017}"
REMOTE_FRAMES="${REMOTE_FRAMES:-$REMOTE_ROOT/MAX_20251017_001}"
REMOTE_BENCH="${REMOTE_BENCH:-$REMOTE_ROOT/拼图结果}"
SERVER_LINK_ROOT="${SERVER_LINK_ROOT:-$ROOT/server/data/input/MAX_20251017}"
MIN_FREE_GB="${MIN_FREE_GB:-35}"
WITH_BENCHMARK="${WITH_BENCHMARK:-1}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30)

if [ -n "${GPU_PASS:-}" ]; then
  command -v sshpass >/dev/null || { echo "需要 sshpass（brew install sshpass）" >&2; exit 1; }
  export SSHPASS="$GPU_PASS"
  USE_SSHPASS=1
else
  USE_SSHPASS=0
fi

remote() {
  if [ "$USE_SSHPASS" = 1 ]; then
    sshpass -e ssh "${SSH_OPTS[@]}" "$HOST" "$@"
  else
    ssh "${SSH_OPTS[@]}" "$HOST" "$@"
  fi
}
rsync_to() {
  local src="$1" dest="$2"
  if [ "$USE_SSHPASS" = 1 ]; then
    sshpass -e rsync -a --partial --stats --exclude '.DS_Store' -e "ssh ${SSH_OPTS[*]}" \
      "${src}/" "$HOST:${dest}/"
  else
    rsync -a --partial --stats --exclude '.DS_Store' -e "ssh ${SSH_OPTS[*]}" \
      "${src}/" "$HOST:${dest}/"
  fi
}

require_local() {
  [ -d "$SRC_FRAMES" ] || { echo "本地不存在: $SRC_FRAMES" >&2; exit 1; }
  local n
  n="$(find "$SRC_FRAMES" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  echo "本地航片目录: $SRC_FRAMES（约 ${n} 个文件）"
}

check_remote() {
  remote "df -h /home/spectrum-mosaic | tail -1; echo '---'; ls -la '$REMOTE_ROOT' 2>/dev/null || echo '(远端尚无 $REMOTE_ROOT)'; du -sh '$REMOTE_FRAMES' 2>/dev/null || true"
}

link_server_paths() {
  remote "mkdir -p '$ROOT/server/data/input' && ln -sfn '$REMOTE_ROOT' '$ROOT/server/data/input/MAX_20251017'"
  echo "页面输入目录填: /data/input/MAX_20251017/MAX_20251017_001"
}

upload() {
  require_local
  local free
  free="$(remote "df -BG --output=avail /home | tail -1 | tr -dc 0-9")"
  echo "/home 剩余 ${free} GB（需要约 ${MIN_FREE_GB} GB）"
  [ "$free" -ge "$MIN_FREE_GB" ] || { echo "空间不足，停止上传" >&2; exit 1; }

  remote "mkdir -p '$REMOTE_FRAMES'"
  echo ">>> rsync MAX_20251017_001 → $REMOTE_FRAMES"
  rsync_to "$SRC_FRAMES" "$REMOTE_FRAMES"

  if [ "$WITH_BENCHMARK" = "1" ] && [ -d "$SRC_BENCH" ]; then
    remote "mkdir -p '$REMOTE_BENCH'"
    echo ">>> rsync 拼图结果 → $REMOTE_BENCH"
    rsync_to "$SRC_BENCH" "$REMOTE_BENCH"
  fi

  link_server_paths
  echo ">>> 完成"
  check_remote
}

case "${1:-upload}" in
  check) check_remote ;;
  upload) upload ;;
  link) link_server_paths ;;
  *)
    echo "用法: $0 [check|upload|link]" >&2
    exit 2
    ;;
esac
