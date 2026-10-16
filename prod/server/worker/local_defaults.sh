# 本机开发默认路径。run.sh / 可视化启动脚本 source 这一处。
# 换机器改这里，或先 export 同名环境变量覆盖。
MS_MOSAIC_HOST_ROOT="${MS_MOSAIC_HOST_ROOT:-/Users/jiangshengping/wwwroot/shenzhen/spectrum}"
MS_MOSAIC_PROD="${MS_MOSAIC_PROD:-$MS_MOSAIC_HOST_ROOT/algorithm/projects/数据融合/prod}"
MS_MOSAIC_CWD="${MS_MOSAIC_CWD:-$MS_MOSAIC_PROD/server/worker}"
MS_MOSAIC_DATA="${MS_MOSAIC_DATA:-$MS_MOSAIC_PROD/server/data}"
MS_MOSAIC_PYTHON="${MS_MOSAIC_PYTHON:-$MS_MOSAIC_HOST_ROOT/algorithm/source/.venv/bin/python}"
MS_MOSAIC_INPUT="${MS_MOSAIC_INPUT:-$MS_MOSAIC_DATA/input/MAX_20251017/MAX_20251017_001}"
MS_MOSAIC_BENCHMARK="${MS_MOSAIC_BENCHMARK:-$MS_MOSAIC_PROD/docs/需求/测试正式数据/MAX_20251017/拼图结果}"
MS_MOSAIC_CACHE="${MS_MOSAIC_CACHE:-$MS_MOSAIC_PROD/runs/full_MAX_20251017_001/cache/features}"
MS_MOSAIC_REUSE_DSM="${MS_MOSAIC_REUSE_DSM:-$MS_MOSAIC_PROD/runs/full_surface_rgb/拼图结果/DSM.tif}"
MS_MOSAIC_RUNS="${MS_MOSAIC_RUNS:-$MS_MOSAIC_DATA/output/runs}"
