#!/bin/bash
# 在有 T4（sm_75）和 CUDA 11 的机器上编出 libsiftgpu.so。不装系统 CUDA 12。
# 编好的 libsiftgpu.so 放在本目录并提交进仓库。它不在源码树里时，同步计算代码会把线上的库清掉，特征提取就退回 CPU。
set -euo pipefail
cd "$(dirname "$0")"
nvcc -allow-unsupported-compiler -O3 -std=c++17 -arch=sm_75 -Xcompiler -fPIC -shared \
  -o libsiftgpu.so cudaImage.cu cudaSiftH.cu sift_api.cu
echo "wrote $(pwd)/libsiftgpu.so"
