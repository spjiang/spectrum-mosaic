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
