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
