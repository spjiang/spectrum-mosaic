#!/usr/bin/env bash
# 在 spectrum 仓库安装 post-commit：提交若动到「数据融合」则自动同步 spectrum-mosaic。
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
SPECTRUM="$(cd "$SRC/../../.." && pwd)"
HOOK="$SPECTRUM/.git/hooks/post-commit"
SYNC="$SRC/scripts/sync_spectrum_mosaic.sh"

if [[ ! -d "$SPECTRUM/.git" ]]; then
  echo "未找到 spectrum 仓库：$SPECTRUM" >&2
  exit 1
fi

MARKER="# spectrum-mosaic-sync"
if [[ -f "$HOOK" ]] && grep -q "$MARKER" "$HOOK"; then
  echo "post-commit 已安装：$HOOK"
  exit 0
fi

if [[ ! -f "$HOOK" ]]; then
  printf '%s\n' '#!/usr/bin/env bash' > "$HOOK"
fi

{
  echo ""
  echo "$MARKER"
  echo '# 数据融合有变更时同步到 https://github.com/spjiang/spectrum-mosaic'
  echo 'if git diff-tree --no-commit-id --name-only -r HEAD | grep -q '"'"'algorithm/projects/数据融合/'"'"'; then'
  echo '  if [[ "${SKIP_MOSAIC_SYNC:-0}" != "1" ]]; then'
  echo "    \"$SYNC\" || echo \"warn: spectrum-mosaic 同步失败（可稍后手动跑 scripts/sync_spectrum_mosaic.sh）\" >&2"
  echo '  fi'
  echo 'fi'
} >> "$HOOK"

chmod +x "$HOOK" "$SYNC"
echo "已安装：$HOOK"
echo "跳过某次同步：SKIP_MOSAIC_SYNC=1 git commit ..."
