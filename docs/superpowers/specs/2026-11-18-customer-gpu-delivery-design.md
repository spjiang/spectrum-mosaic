# 客户交付包启用 NVIDIA GPU

**日期：** 2026-11-18  
**状态：** 已确认  
**范围：** amd64 客户 worker 镜像、交付 compose 覆盖层、启动脚本；中达瑞和 Windows 现场热修  
**非目标：** arm64 GPU；改算法；把 GPU 写进默认 `docker-compose.yml`（没卡的机器会起不来）

## 背景

中达瑞和现场机（`CHINAMI-IPKILU3`）有 **NVIDIA GeForce RTX 4070 Ti SUPER**、64 GB 内存、i5-12600KF。Windows 能认出这张卡，但新建处理「任务 GPU」只有「自动 / 不用 GPU」。

页面列出的是 **worker 容器** 用 CuPy 探测到的设备。当前客户包是 CPU 镜像（`python:3.11-slim`，无 CuPy），compose 也不把 NVIDIA 设备交给容器。

## 决策

| 项 | 选择 |
| --- | --- |
| amd64 worker | CUDA 12.2 运行库 + `requirements-gpu.txt`（CuPy）+ 现有 Cython/许可 |
| arm64 worker | 仍用 CPU Dockerfile |
| 默认 compose | 不写 `gpus`，没卡的机器照常启动 |
| GPU 覆盖 | 交付包带 `docker-compose.gpu.yml`，只给 worker 加设备与环境变量 |
| 启动 | `nvidia-smi` 成功则叠加覆盖层；失败或 Docker 拒绝 GPU 则退回 CPU 并警告 |
| 镜像 tag | 仍为 `spectrum-mosaic-worker:<版本>-amd64`，不另开 `-gpu` tag |
| 现场 | 用新 amd64 构建热修 `E:\spectrum-mosaic`，建议版本 `0.1.0-rc2` |

## 成功标准

1. 有 NVIDIA 且 Docker 能传 GPU 时，「任务 GPU」出现 `GPU 0 … RTX 4070 Ti SUPER`（或本机实际卡名）。
2. 无 NVIDIA 或传 GPU 失败时，`start.sh` / `start.ps1` 仍能拉起服务，任务走 CPU。
3. 计算节点页列出同一张卡。选「自动」时特征提取 / 平差 / 密集匹配走 GPU；像对匹配仍走 CPU。

## 现场热修（中达瑞和）

发版台构建 amd64 成功后：把新 `worker-image.tar` 和 `docker-compose.gpu.yml` 放到 `E:\spectrum-mosaic`，`docker load` 后执行 `start.cmd`。不要只改网页、不换镜像。
