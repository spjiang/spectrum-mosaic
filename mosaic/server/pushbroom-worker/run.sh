#!/usr/bin/env bash
# 推扫拼图本地命令行：不经队列，直接用镜像核对或处理一个架次。
#
# 用法：
#   ./run.sh build                                    构建镜像 server-pushbroom-worker
#   ./run.sh check <架次目录>                         核对三件套并切航带，不写文件
#   ./run.sh run <架次目录> <输出目录> [选项...]      处理，选项同 python -m pb_mosaic run
#       例：./run.sh run ~/data/FigSpec_260414_134505 /tmp/pb-0414 --bands 48,150,208
#   ./run.sh calibrate <架次目录> [<架次目录>...]     用往返航带自检安装参数，给出 PB_SENSOR_* 建议值
#   ./run.sh test [pytest 参数...]                    跑单元测试（含真实数据用例，缺数据自动跳过）
#
# 环境变量：PB_IMAGE（默认 server-pushbroom-worker）、PB_MAX_MEMORY_GB（默认 8）、
#           PB_SENSOR_*（本机标定，见 README「几何标定」，原样传进容器）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$ROOT/../../.." && pwd)"
IMAGE="${PB_IMAGE:-server-pushbroom-worker}"

SENSOR_ENV=()
for name in PB_SENSOR_SAMPLE_SIGN PB_SENSOR_ALONG_OFFSET_M PB_SENSOR_BORESIGHT_PX PB_SENSOR_GSD_CROSS_M PB_SENSOR_FLIGHT_HEIGHT_M; do
  if [[ -n "${!name:-}" ]]; then SENSOR_ENV+=(-e "$name=${!name}"); fi
done

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | tail -n +2
  exit "${1:-0}"
}

abs() { (cd "$1" && pwd); }

cmd="${1:-}"
[[ -n "$cmd" ]] || usage 1
shift

case "$cmd" in
  build)
    exec docker build -t "$IMAGE" "$ROOT"
    ;;
  check)
    [[ $# -ge 1 ]] || usage 1
    in="$(abs "$1")"
    exec docker run --rm ${SENSOR_ENV[@]+"${SENSOR_ENV[@]}"} -v "$in":/data/input/flight:ro -v "$ROOT/pb_mosaic":/app/pb_mosaic:ro \
      "$IMAGE" python -m pb_mosaic check --input /data/input/flight
    ;;
  calibrate)
    [[ $# -ge 1 ]] || usage 1
    mounts=() inputs=() seen=" " k=0
    for dir in "$@"; do
      name="$(basename "$(abs "$dir")")"
      [[ "$seen" == *" $name "* ]] && name="${name}_$k"
      seen+="$name "
      mounts+=(-v "$(abs "$dir")":"/data/input/$name":ro)
      inputs+=(--input "/data/input/$name")
      k=$((k + 1))
    done
    exec docker run --rm ${SENSOR_ENV[@]+"${SENSOR_ENV[@]}"} "${mounts[@]}" -v "$ROOT/pb_mosaic":/app/pb_mosaic:ro \
      "$IMAGE" python -m pb_mosaic calibrate "${inputs[@]}"
    ;;
  run)
    [[ $# -ge 2 ]] || usage 1
    in="$(abs "$1")"
    mkdir -p "$2"
    out="$(abs "$2")"
    shift 2
    exec docker run --rm -e PB_MAX_MEMORY_GB="${PB_MAX_MEMORY_GB:-8}" ${SENSOR_ENV[@]+"${SENSOR_ENV[@]}"} \
      -v "$in":/data/input/flight:ro -v "$out":/data/output/run -v "$ROOT/pb_mosaic":/app/pb_mosaic:ro \
      "$IMAGE" python -m pb_mosaic run --input /data/input/flight --output /data/output/run "$@"
    ;;
  test)
    exec docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$REPO":/repo:ro -w /repo/mosaic/server/pushbroom-worker \
      "$IMAGE" python -m pytest -p no:cacheprovider -W ignore::PendingDeprecationWarning "$@"
    ;;
  -h|--help|help)
    usage 0
    ;;
  *)
    usage 1
    ;;
esac
