#!/usr/bin/env bash
# T4 服务器 GPU 调度验收：上传 MAX_20251017 的 Color + 白板帧与商业成果 → 同步代码 → 装环境 → 探测 → 对拍 → 后台全流程。
#   GPU_HOST=root@10.17.19.198 GPU_PASS=... bash t4_mosaic_trial.sh [upload|code|setup|probe|test|run|all]
# 只写 /home/spectrum-mosaic；/home 剩余不足 MIN_FREE_GB 时不上传。
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA_SRC="${DATA_SRC:-$HERE/../data/input/MAX_20251017}"
HOST="${GPU_HOST:?请设置 GPU_HOST}"
ROOT="${ROOT:-/home/spectrum-mosaic}"
MIN_FREE_GB="${MIN_FREE_GB:-15}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30)
if [ -n "${GPU_PASS:-}" ]; then export SSHPASS="$GPU_PASS"; WRAP=(sshpass -e); else WRAP=(); fi
remote() { "${WRAP[@]}" ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }
check() { bash "$HERE/scripts/push_gpu_host.sh" "$@"; }

upload() {
  local free
  free=$(remote "df -BG --output=avail /home | tail -1 | tr -dc 0-9")
  echo "/home 剩余 ${free} GB"
  [ "$free" -ge "$MIN_FREE_GB" ] || { echo "空间不足 ${MIN_FREE_GB} GB，停止上传" >&2; exit 1; }
  local list
  list="$(mktemp)"
  # 商业成果先传：比对和格网锁定都要它
  (cd "$DATA_SRC" && { ls 拼图结果 | grep -E 'group0|DSM' | sed 's#^#拼图结果/#'; \
                       ls MAX_20251017_001 | grep -E '_Color_|_W\.tif$' | sed 's#^#MAX_20251017_001/#'; }) > "$list"
  echo "上传 $(wc -l < "$list") 个文件"
  remote "mkdir -p '$ROOT/data/MAX_20251017'"
  "${WRAP[@]}" rsync -a --partial --stats --files-from="$list" -e "ssh ${SSH_OPTS[*]}" \
    "$DATA_SRC/" "$HOST:$ROOT/data/MAX_20251017/"
  rm -f "$list"
}

run_trial() {
  local out="$ROOT/runs/t4_max_rgb_$(date +%Y%m%d_%H%M%S)"
  MOSAIC_GPU_TRIAL_STAGES=S5_ortho check run "$out" \
    --input-dir "$ROOT/data/MAX_20251017/MAX_20251017_001" \
    --preset rgb_preview --edge-trim-m 0 \
    --terrain-margin-lo-m 30 --terrain-margin-hi-m 50 --terrain-min-half-span-m 80 \
    --benchmark-dir "$ROOT/data/MAX_20251017/拼图结果" \
    --grid-reference "$ROOT/data/MAX_20251017/拼图结果" \
    --gpu auto --cpus 32 --memory-gb 60
  echo "$out"
}

case "${1:-all}" in
  upload) upload ;;
  code) check probe >/dev/null 2>&1 || true ;;
  setup) check setup ;;
  probe) check probe ;;
  test) shift; check test "$@" ;;
  run) run_trial ;;
  all)
    upload
    check setup
    check probe
    check test tests/test_gpu.py tests/test_xp.py tests/test_ortho_gpu.py
    run_trial
    ;;
  *) echo "用法：$0 upload|setup|probe|test|run|all" >&2; exit 2 ;;
esac
