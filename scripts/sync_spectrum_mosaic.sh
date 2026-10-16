#!/usr/bin/env bash
# 把「数据融合」可部署树同步到独立仓库 spectrum-mosaic 并推送。
# 用法：
#   ./scripts/sync_spectrum_mosaic.sh           # 有变更则 commit + push
#   ./scripts/sync_spectrum_mosaic.sh --dry-run  # 只看会同步什么
#   SKIP_PUSH=1 ./scripts/sync_spectrum_mosaic.sh  # 只本地提交不同步远程
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
  --exclude 'prod/runs/'
  --exclude 'prod/server/worker/runs/'
  --exclude 'prod/server/frontend/dist/'
  --exclude 'prod/server/.run/'
  --exclude 'prod/server/data/input/**'
  --exclude 'prod/server/data/output/runs/**'
  --exclude 'prod/server/data/.inspect/'
  --exclude 'prod/server/data/.worker/'
  --exclude 'prod/visual_server/'
  --exclude 'prod/docs/需求/'
  --exclude 'test/work/'
  --exclude 'test/out/'
  --exclude 'test/input_bench/'
  --exclude 'qgis-test/'
  --exclude 'docs/颜色验证/'
  --exclude '*.tif'
  --exclude '*.TIF'
  --exclude '*.tiff'
  --exclude '*.jpg'
  --exclude '*.JPG'
  --exclude '*.jpeg'
  --exclude '*.png'
  --exclude '*.zip'
  --exclude '*.npz'
  --exclude '*.las'
  --exclude '*.laz'
)

if [[ ! -d "$DST/.git" ]]; then
  echo "克隆 $REPO_URL → $DST"
  git clone "$REPO_URL" "$DST"
fi

echo "同步 $SRC → $DST"
if [[ "$DRY" -eq 1 ]]; then
  rsync -a --delete --dry-run --itemize-changes \
    "${RSYNC_EXCLUDES[@]}" \
    --exclude '.git/' \
    "$SRC/" "$DST/"
  exit 0
fi

rsync -a --delete \
  "${RSYNC_EXCLUDES[@]}" \
  --exclude '.git/' \
  "$SRC/" "$DST/"

# 保证数据目录可被 clone 出来，又不上真实影像
mkdir -p "$DST/prod/server/data/input" "$DST/prod/server/data/output/runs"
touch "$DST/prod/server/data/input/.gitkeep" "$DST/prod/server/data/output/runs/.gitkeep"

# 顶层部署说明（覆盖旧文也可）
cat > "$DST/README.md" <<'EOF'
# spectrum-mosaic

多光谱拼图管控台（frontend / backend / worker + PostgreSQL / RabbitMQ）。

本仓库由 monorepo `spectrum` 中 `algorithm/projects/数据融合` 同步而来，**不含测区原始数据与跑数产物**。

## 快速部署

```bash
cd prod/server
cp .env.example .env
# 按需改密码 / JWT_SECRET；把测区挂到 data/input/<测区>/<架次>/
docker compose up -d --build
```

- 前端：http://localhost:8080  
- API：http://localhost:8000/api/health  
- RabbitMQ：http://localhost:15672（默认 mosaic / mosaic_secret）  
- 管理员：见 `.env.example`（请立刻改掉）

测区数据放到 `prod/server/data/input/<测区名>/<架次>/`，或在 `.env` 里用 `DATASET_*` 挂载本机路径（见 `docker-compose.yml`）。

更细的说明见 `prod/server/README.md` 与 `prod/server/worker/docs/cli-usage.md`。
EOF

# .env.example 去掉本机绝对路径，方便他人部署
if [[ -f "$DST/prod/server/.env.example" ]]; then
  sed -i.bak 's|^DATASET_MAX_20251017=.*|DATASET_MAX_20251017=./data/input/MAX_20251017/MAX_20251017_001|' \
    "$DST/prod/server/.env.example" && rm -f "$DST/prod/server/.env.example.bak"
fi

cd "$DST"
git add -A
if git diff --cached --quiet; then
  echo "无变更，跳过提交"
  exit 0
fi

PARENT_MSG=""
if git -C "$(cd "$SRC/../../.." && pwd)" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  PARENT_MSG="$(git -C "$(cd "$SRC/../../.." && pwd)" log -1 --pretty=%s 2>/dev/null || true)"
fi
MSG="sync: ${PARENT_MSG:-update deployable mosaic tree}"

git -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
    -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
    commit -m "$MSG"

if [[ "${SKIP_PUSH:-0}" == "1" ]]; then
  echo "已本地提交，SKIP_PUSH=1 未推送"
  exit 0
fi

git push -u origin HEAD
echo "已推送到 $REPO_URL"
