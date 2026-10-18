#!/usr/bin/env bash
# 在共享 GPU 服务器上做 GPU 对拍验收，不建 Docker 镜像、不动系统目录。
# 只写 $ROOT（默认 /home/spectrum-mosaic）；CPU 用 taskset 限核，内存用 systemd-run 限额，GPU 用 CUDA_VISIBLE_DEVICES 选卡。
#
# 用法（在服务器上）：
#   bash gpu_host_check.sh setup     # 装 micromamba + Python 3.11 + requirements-gpu.txt
#   bash gpu_host_check.sh probe     # 显卡探测
#   bash gpu_host_check.sh test      # 全部 worker 测试（含 @pytest.mark.gpu 对拍）
set -euo pipefail

ROOT="${ROOT:-/home/spectrum-mosaic}"
CODE="${CODE:-$ROOT/worker}"
ENV_DIR="$ROOT/env"
MAMBA="$ROOT/bin/micromamba"
CORES="${CORES:-0-31}"
MEM_MAX="${MEM_MAX:-64G}"
GPUS="${GPUS:-0}"

export MAMBA_ROOT_PREFIX="$ROOT/mamba"
export PIP_CACHE_DIR="$ROOT/pip-cache"
export TMPDIR="$ROOT/tmp"
mkdir -p "$ROOT/bin" "$TMPDIR" "$PIP_CACHE_DIR"

run_limited() {
  if command -v systemd-run >/dev/null 2>&1; then
    local ver prop
    ver=$(systemctl --version | head -1 | tr -dc 0-9)
    # cgroup v1 的 systemd（CentOS 7 为 219）只认 MemoryLimit；231 起才有 MemoryMax
    if [ "${ver:-0}" -ge 231 ]; then prop=MemoryMax; else prop=MemoryLimit; fi
    systemd-run --scope --quiet -p "$prop=$MEM_MAX" \
      env CUDA_VISIBLE_DEVICES="$GPUS" taskset -c "$CORES" "$@"
  else
    CUDA_VISIBLE_DEVICES="$GPUS" taskset -c "$CORES" "$@"
  fi
}

py() { "$ENV_DIR/bin/python" "$@"; }

case "${1:-}" in
  setup)
    # 机器上已有 conda 就借用它的程序（环境与包缓存都放在 $ROOT，不改其配置）；否则下 micromamba。
    # micromamba 2.x 在 CentOS 7 上解完依赖即段错误，所以优先 conda。
    SOLVER="${CONDA_EXE_PATH:-}"
    if [ -z "$SOLVER" ]; then
      for c in /root/miniconda3/bin/conda /opt/conda/bin/conda; do [ -x "$c" ] && SOLVER="$c" && break; done
    fi
    if [ -z "$SOLVER" ]; then
      [ -x "$MAMBA" ] || curl -fsSL https://micro.mamba.pm/api/micromamba/linux-64/1.5.10 | tar -xj -C "$ROOT" bin/micromamba
      SOLVER="$MAMBA"
    fi
    export CONDA_PKGS_DIRS="$ROOT/pkgs" CONDA_ENVS_PATH="$ROOT"
    # CentOS 7 是 glibc 2.17，新版 numpy/rasterio 的 pip 轮子要 2.28；重依赖走 conda-forge（兼容 2.17）
    if [ ! -x "$ENV_DIR/bin/python" ]; then
      # cupy-core 不带 NVRTC，须另装 cuda-nvrtc；不拉 cuBLAS/cuFFT 等数学库（省约 1 GB）；GPU 代码只用逐元素与 scatter
      "$SOLVER" create -y -p "$ENV_DIR" -c "${CONDA_CHANNEL:-conda-forge}" --override-channels \
        python=3.11 pip "numpy<2.3" scipy rasterio pillow tifffile reportlab matplotlib-base \
        pyproj shapely pymaxflow pika pytest pypdf "cupy-core>=13,<14" cuda-nvrtc "cuda-version=12.2"
    fi
    py -m pip install -i "${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}" "opencv-python-headless<4.11"
    "$SOLVER" clean -y --all >/dev/null 2>&1 || true
    ;;
  probe)
    cd "$CODE"
    nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap --format=csv
    CUDA_VISIBLE_DEVICES="$GPUS" py -c "from ms_mosaic.gpu import probe, summary; print(probe()); print(summary())"
    ;;
  test)
    cd "$CODE"
    shift
    run_limited "$ENV_DIR/bin/python" -m pytest -o addopts="" -q -rs -m "not slow" "$@"
    ;;
  run)
    # 后台跑一次拼图：bash gpu_host_check.sh run <输出目录> [ms_mosaic 参数...]；日志在 <输出目录>.log
    cd "$CODE"
    shift
    out="${1:?请给输出目录}"
    shift
    mkdir -p "$(dirname "$out")"
    nohup bash -c "$(declare -f run_limited); CORES='$CORES' MEM_MAX='$MEM_MAX' GPUS='$GPUS' \
      run_limited env MOSAIC_GPU_TRIAL_STAGES='${MOSAIC_GPU_TRIAL_STAGES:-}' PYTHONPATH='$CODE' \
      OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 MALLOC_ARENA_MAX=2 \
      '$ENV_DIR/bin/python' -m ms_mosaic --output-dir '$out' $*" > "$out.log" 2>&1 &
    echo "已在后台启动 pid=$! 日志 $out.log"
    ;;
  *)
    echo "用法：$0 setup|probe|test|run" >&2
    exit 2
    ;;
esac
