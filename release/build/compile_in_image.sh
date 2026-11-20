#!/usr/bin/env bash
# 在客户镜像构建阶段执行：把算法模块编成 .so，并删除对应 .py
# 用法: compile_in_image.sh [源码根目录] [模块清单] [包名]，包名默认 ms_mosaic（框幅）
set -euo pipefail
WORKER="${1:-/build/worker}"
LIST="${2:-/build/release/build/cython_modules.txt}"
PACKAGE="${3:-ms_mosaic}"
cd "$WORKER"
CYTHON_MODULES_FILE="$LIST" CYTHON_PACKAGE="$PACKAGE" python /build/release/build/setup_cython.py build_ext --inplace
while IFS= read -r name || [ -n "$name" ]; do
  name="$(echo "$name" | tr -d '[:space:]')"
  [[ -z "$name" || "$name" == \#* ]] && continue
  rm -f "${PACKAGE}/${name}.py" "${PACKAGE}/${name}.c"
done < "$LIST"
# 清理构建垃圾；字节码里有源码信息，不能留
rm -rf build
find "$PACKAGE" -name '*.html' -delete || true
find "$PACKAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + || true
echo "compiled modules:"
find "$PACKAGE" -name '*.so' | sort
