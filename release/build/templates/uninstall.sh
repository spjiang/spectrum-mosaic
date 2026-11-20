#!/usr/bin/env sh
# 卸载本交付包的容器和镜像。不删除 data、pgdata、licenses、.env。
set -eu
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker。" >&2
  exit 1
fi

echo ">>> 停止并删除本交付包的容器和镜像"
echo ">>> 保留 data、pgdata、licenses、.env，不删除用户数据"

set -- -f docker-compose.yml
if [ -f docker-compose.gpu.yml ]; then
  set -- "$@" -f docker-compose.gpu.yml
fi
# 推扫 worker 在 profile 里，不点名时 down 不会处理它
if grep -q 'profiles: \["pushbroom"\]' docker-compose.yml; then
  set -- "$@" --profile pushbroom
fi

# 禁止追加 -v 或 --volumes，否则会清掉命名数据卷。
docker compose "$@" down --rmi all --remove-orphans

echo ">>> 卸载完成。以下内容未删除："
echo ">>>   data/      航片和成果"
echo ">>>   pgdata/    数据库"
echo ">>>   licenses/  许可文件"
echo ">>>   .env       本机配置"
echo ">>> 再次使用时执行 ./start.sh。镜像已删除时会重新导入，并沿用上述数据。"
