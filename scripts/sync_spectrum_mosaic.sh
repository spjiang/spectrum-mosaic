#!/usr/bin/env bash
# 把「数据融合」同步到独立仓库 spectrum-mosaic 并推送。
#
# 固定排除：
#   - prod/server/data 测区数据（只留空目录）
#   - .env / node_modules / .venv / __pycache__ 等
#   - 单文件 >99MB（GitHub 硬限）
#
# 默认再排除「同类大数据集」（约数十 GB，GitHub 放不下）。
# 若坚持全部上传：INCLUDE_ALL_DATASETS=1 ./scripts/sync_spectrum_mosaic.sh
# （仍会跳过单文件 >99MB；总量过大时 GitHub 仍可能拒绝）
#
# 用法：
#   ./scripts/sync_spectrum_mosaic.sh
#   ./scripts/sync_spectrum_mosaic.sh --dry-run
#   SKIP_PUSH=1 ./scripts/sync_spectrum_mosaic.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
REPO_URL="${SPECTRUM_MOSAIC_URL:-git@github.com:spjiang/spectrum-mosaic.git}"
DST="${SPECTRUM_MOSAIC_DIR:-$(cd "$SRC/../../../.." && pwd)/spectrum-mosaic}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude '.env'
  --exclude '**/.env'
  --exclude '**/__pycache__/'
  --exclude '**/.pytest_cache/'
  --exclude '**/.venv/'
  --exclude '**/venv/'
  --exclude '**/node_modules/'
  --exclude '**/.DS_Store'
  --exclude 'prod/server/.run/'
  --exclude 'prod/server/frontend/dist/'
  # 唯一指定的数据集目录
  --exclude 'prod/server/data/input/**'
  --exclude 'prod/server/data/output/**'
  --exclude 'prod/server/data/.inspect/'
  --exclude 'prod/server/data/.worker/'
)

# 与 data 同类、但不在 data/ 下的海量二进制（默认排除，否则无法推 GitHub）
EXTRA_DATASET_EXCLUDES=(
  --exclude 'prod/docs/需求/拼图数据集/'
  --exclude 'prod/docs/需求/测试结果验证数据/'
  --exclude 'prod/docs/需求/测试正式数据/'
  --exclude 'test/work/'
  --exclude 'test/out/'
  --exclude 'test/input_bench/'
)

if [[ "${INCLUDE_ALL_DATASETS:-0}" == "1" ]]; then
  echo "INCLUDE_ALL_DATASETS=1：尝试同步 docs/需求 与 test 下大数据集（仍跳过单文件>99MB）"
else
  RSYNC_EXCLUDES+=("${EXTRA_DATASET_EXCLUDES[@]}")
  echo "默认排除同类大数据集目录（设 INCLUDE_ALL_DATASETS=1 可关闭该排除）"
fi

MAX_BYTES=$((99 * 1024 * 1024))

if [[ ! -d "$DST/.git" ]]; then
  echo "克隆 $REPO_URL → $DST"
  git clone "$REPO_URL" "$DST"
fi

echo "同步 $SRC → $DST"

OVERSIZE_LIST="$(mktemp)"
OVERSIZE_EXCLUDES=()
while IFS= read -r -d '' f; do
  rel="${f#"$SRC"/}"
  case "$rel" in
    prod/server/data/*) continue ;;
  esac
  printf '%s\n' "$rel" >> "$OVERSIZE_LIST"
  OVERSIZE_EXCLUDES+=(--exclude "$rel")
done < <(find "$SRC" -type f -size +${MAX_BYTES}c -print0 2>/dev/null)

if [[ -s "$OVERSIZE_LIST" ]]; then
  echo "超过 GitHub 100MB 已跳过（$(wc -l < "$OVERSIZE_LIST") 个），详见同步日志。"
fi

rsync_cmd=(rsync -a --delete "${RSYNC_EXCLUDES[@]}" "${OVERSIZE_EXCLUDES[@]}" --exclude '.git/')
if [[ "$DRY" -eq 1 ]]; then
  "${rsync_cmd[@]}" --dry-run --itemize-changes "$SRC/" "$DST/" | tail -100
  rm -f "$OVERSIZE_LIST"
  exit 0
fi

"${rsync_cmd[@]}" "$SRC/" "$DST/"
rm -f "$OVERSIZE_LIST"

# rsync 默认不删除「被 exclude 的目标路径」，手动清运行时目录
rm -rf "$DST/prod/server/.run" "$DST/prod/server/frontend/dist"

mkdir -p "$DST/prod/server/data/input" "$DST/prod/server/data/output/runs"
find "$DST/prod/server/data/input" "$DST/prod/server/data/output" \
  -type f ! -name '.gitkeep' ! -name '.gitignore' -delete 2>/dev/null || true
find "$DST/prod/server/data" -mindepth 1 -maxdepth 1 -type d \( -name '.inspect' -o -name '.worker' \) \
  -exec rm -rf {} + 2>/dev/null || true
touch "$DST/prod/server/data/input/.gitkeep" "$DST/prod/server/data/output/runs/.gitkeep"

# 需求下被排除的大数据集：留 README 说明
mkdir -p "$DST/prod/docs/需求"
cat > "$DST/prod/docs/需求/README.md" <<'EOF'
# 需求与测区数据

本目录上传需求文档与材料。以下**测区/验证大数据集**体积过大，不同步到 GitHub，请本机自备：

- `拼图数据集/`
- `测试结果验证数据/`
- `测试正式数据/`

运行时测区请放到 `prod/server/data/input/<测区>/<架次>/`（该目录本身也不入库）。
EOF

cat > "$DST/prod/server/data/.gitignore" <<'EOF'
# 测区数据不入库；仅保留空目录
input/**
!input/.gitkeep
output/**
!output/runs/
!output/runs/.gitkeep
.inspect/
.worker/
**/.DS_Store
EOF

cat > "$DST/README.md" <<'EOF'
# spectrum-mosaic

多光谱拼图管控台（frontend / backend / worker + PostgreSQL / RabbitMQ）。

由 `spectrum` 的 `algorithm/projects/数据融合` 同步而来。  
**不包含** `prod/server/data` 测区数据；需求下的超大拼图/验证数据集也不入库。

## 快速部署

```bash
cd prod/server
cp .env.example .env
docker compose up -d --build
```

测区放到 `prod/server/data/input/<测区>/<架次>/`。详见 `prod/server/README.md`。
EOF

if [[ -f "$DST/prod/server/.env.example" ]]; then
  sed -i.bak 's|^DATASET_MAX_20251017=.*|DATASET_MAX_20251017=./data/input/MAX_20251017/MAX_20251017_001|' \
    "$DST/prod/server/.env.example" && rm -f "$DST/prod/server/.env.example.bak"
fi

cd "$DST"
# 估算将提交体积
approx="$(git add -A -n 2>/dev/null | wc -l | tr -d ' ')"
git add -A
git reset -q -- prod/server/data/input prod/server/data/output 2>/dev/null || true
git add -f prod/server/data/input/.gitkeep prod/server/data/output/runs/.gitkeep prod/server/data/.gitignore \
  prod/docs/需求/README.md 2>/dev/null || true

# 拒绝把已跟踪的超大文件再推上去
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  if [[ -f "$f" ]]; then
    sz=$(wc -c < "$f" | tr -d ' ')
    if (( sz > MAX_BYTES )); then
      git rm -r --cached -f -- "$f" 2>/dev/null || true
      echo "已从索引移除超限文件: $f"
    fi
  fi
done < <(git diff --cached --name-only -z | tr '\0' '\n')

if git diff --cached --quiet; then
  echo "无变更，跳过提交"
  exit 0
fi

# 体积保护：暂存区 >1.5GiB 则中止（避免撑爆 GitHub）
blob_bytes="$(git diff --cached --numstat | awk '{if ($1!="-") add+=$1} END{print add+0}')"
# numstat 是行数不是字节；改用 du on staged files
staged_bytes=0
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  staged_bytes=$((staged_bytes + $(wc -c < "$f" | tr -d ' ')))
done < <(git diff --cached --name-only)
echo "暂存约 $(python3 -c "print(f'{${staged_bytes}/1024**3:.2f}')") GiB（文件列表约 ${approx} 项）"
LIMIT=$((1500 * 1024 * 1024))
if (( staged_bytes > LIMIT )) && [[ "${FORCE_LARGE_SYNC:-0}" != "1" ]]; then
  echo "暂存超过 1.5GiB，已中止。确认要推请 FORCE_LARGE_SYNC=1，或保持默认排除大数据集。" >&2
  git reset -q
  exit 1
fi

PARENT_MSG=""
SPECTRUM_ROOT="$(cd "$SRC/../../.." && pwd)"
if git -C "$SPECTRUM_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  PARENT_MSG="$(git -C "$SPECTRUM_ROOT" log -1 --pretty=%s 2>/dev/null || true)"
fi
MSG="sync: ${PARENT_MSG:-update mosaic (exclude server/data)}"

git -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
    -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
    commit -m "$MSG"

if [[ "${SKIP_PUSH:-0}" == "1" ]]; then
  echo "已本地提交，SKIP_PUSH=1 未推送"
  exit 0
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]] && git show-ref --verify --quiet refs/remotes/origin/main; then
  git branch -M main
  branch=main
fi
git push -u origin "$branch"
# GitHub 默认分支若仍是 master，一并更新，避免只看 commits/master 时落后
if [[ "$branch" == "main" ]]; then
  git push origin main:master || true
fi
echo "已推送到 $REPO_URL ($branch)"
