from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mosaic:mosaic_secret@127.0.0.1:5432/mosaic_visual"
    rabbitmq_url: str = "amqp://mosaic:mosaic_secret@127.0.0.1:5672/"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    data_roots: str = "/data"
    default_input_dir: str = "/data/input/MAX_20251017/MAX_20251017_001"
    default_output_dir: str = "/data/output/runs"
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str = "admin123"
    cli_guide_path: str = "/cli_docs/cli-usage.md"
    # 主程序在 Worker 容器：/app 即 server/worker
    cli_python: str = "python"
    cli_module: str = "ms_mosaic"
    cli_cwd: str = "/app"
    cli_probe_args: str = "-h"
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    hold_queue_while_awaiting: bool = False
    # compose 里 Worker 容器执行主程序，backend 不再在容器内探测本机 python
    cli_probe: bool = True

    def data_root_list(self) -> list[str]:
        return [p.strip() for p in self.data_roots.split(",") if p.strip()]

    def cors_origin_list(self) -> list[str]:
        return [p.strip() for p in self.cors_origins.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
