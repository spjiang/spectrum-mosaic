# 发版台 server

FastAPI + 前端 + PostgreSQL。

```bash
cp .env.example .env
docker compose up -d --build
```

- 页面 http://localhost:8180
- API http://localhost:8100/api/health

构建客户镜像需要本机 Docker，并把 `docker.sock` 挂进 backend（见 compose）。私钥在 `../keys/`，产物在 `../artifacts/`。

## 迁机

1. `docker volume` 导出/拷贝 `release_pgdata`
2. 拷贝 `release/keys/` 与 `release/artifacts/`
3. 在新机器 `docker compose up -d`
