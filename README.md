# spectrum-mosaic

多光谱拼图管控台（frontend / backend / worker + PostgreSQL / RabbitMQ）。

由 `spectrum` 的 `algorithm/projects/数据融合` 同步而来。  
**不包含** `mosaic/server/data` 测区数据；需求下的超大拼图/验证数据集也不入库。

## 目录

| 目录 | 用途 |
| --- | --- |
| `mosaic/server` | 拼图产品（开发与运行） |
| `release/` | 内部发版台：编译客户镜像、签发离线许可、打交付包（只装在我方） |
| `docs/` | 文档与规格 |

## 快速部署（拼图）

```bash
cd mosaic/server
cp .env.example .env
docker compose up -d --build
```

测区放到 `mosaic/server/data/input/<测区>/<架次>/`。详见 `mosaic/server/README.md`。

发版台见 `release/README.md`。

## 测试（推荐在 Docker 内）

本机若未安装 `requirements.txt` 全量依赖，或 PyNaCl 与 CPU 架构不一致，请用容器跑单测：

```bash
./mosaic/server/scripts/test_backend.sh
./mosaic/server/scripts/test_worker.sh
./release/server/scripts/test_backend.sh
```

规格与需求对照见 `docs/specs/README.md`。
