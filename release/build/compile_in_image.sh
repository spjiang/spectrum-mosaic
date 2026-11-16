#!/usr/bin/env bash
# 在客户镜像构建阶段执行：把算法模块编成 .so，并删除对应 .py
set -euo pipefail
WORKER="${1:-/build/worker}"
LIST="${2:-/build/release/build/cython_modules.txt}"
cd "$WORKER"
python /build/release/build/setup_cython.py build_ext --inplace
while IFS= read -r name || [ -n "$name" ]; do
  name="$(echo "$name" | tr -d '[:space:]')"
  [[ -z "$name" || "$name" == \#* ]] && continue
  rm -f "ms_mosaic/${name}.py" "ms_mosaic/${name}.c"
done < "$LIST"
# 清理构建垃圾
rm -rf build
find ms_mosaic -name '*.html' -delete || true
echo "compiled modules:"
find ms_mosaic -name '*.so' | sort
