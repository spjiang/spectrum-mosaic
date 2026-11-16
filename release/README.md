# 内部发版台（release）

只部署在我方环境。负责客户镜像构建登记、客户档案、离线许可签发、交付包下载。  
拼图产品本身在 `../mosaic/server`。

## 快速启动

```bash
cd server
cp .env.example .env
docker compose up -d --build
```

- 发版台页面：http://localhost:8180  
- API：http://localhost:8100/api/health  
- 默认管理员：见 `.env` 的 `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD`

首次签发前会自动在 `keys/` 生成 Ed25519 密钥对（私钥勿提交、勿给客户）。

## 迁机

同时迁移：

1. PostgreSQL 卷 `release_pgdata`
2. `release/artifacts/`（私钥）
3. `release/artifacts/`（镜像 tar、许可、交付 zip）

详见 `server/README.md`。
