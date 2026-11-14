#!/usr/bin/env bash
# MAX 多光谱拼图。输入目录只读，成果写到 --output-dir。
# 旗标名与 Web「处理方案」param key 一致（如 --input-dir ↔ input_dir）。
#
# 用法（在主程序目录下，或给脚本绝对路径）：
#   ./run.sh
#   ./run.sh --max-frames 12 --bands Color
#   ./run.sh --workers-dense 8 --bands Color,550nm
#   ./run.sh --output-dir runs/my_run
#   ./run.sh --input-dir /path/to/MAX_xxx --output-dir /path/to/out
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=local_defaults.sh
source "$ROOT/local_defaults.sh"
PY="$MS_MOSAIC_PYTHON"
INPUT_DEFAULT="$MS_MOSAIC_INPUT"

has_input=0
has_out=0
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir)
      has_input=1
      args+=(--input-dir "$2")
      shift 2
      ;;
    --output-dir)
      has_out=1
      args+=(--output-dir "$2")
      shift 2
      ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

if [[ $has_input -eq 0 ]]; then
  args+=(--input-dir "$INPUT_DEFAULT")
fi
if [[ $has_out -eq 0 ]]; then
  stamp="$(date +%Y%m%d_%H%M%S)"
  args+=(--output-dir "$MS_MOSAIC_RUNS/manual_$stamp")
fi

if [[ ! -x "$PY" ]]; then
  echo "找不到 Python: $PY" >&2
  echo "请先准备 algorithm/source/.venv，或设置 MS_MOSAIC_PYTHON" >&2
  exit 1
fi

echo "python  $PY"
echo "workdir $ROOT"
printf 'run     '
printf '%q ' "$PY" -m ms_mosaic "${args[@]}"
echo
cd "$ROOT"
export PYTHONUNBUFFERED=1
exec "$PY" -m ms_mosaic "${args[@]}"
