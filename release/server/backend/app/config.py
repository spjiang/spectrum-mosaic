from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://release:release_secret@127.0.0.1:5433/release_console"
    jwt_secret: str = "change-me-release-jwt"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str = "admin123"
    cors_origins: str = "http://localhost:8180,http://127.0.0.1:8180,http://localhost:5174"
    keys_dir: str = "/keys"
    artifacts_dir: str = "/artifacts"
    mosaic_server_root: str = "/mosaic/server"
    release_build_root: str = "/release/build"
    product_name: str = "spectrum-mosaic"
    worker_image_repo: str = "spectrum-mosaic-worker"
    pushbroom_worker_image_repo: str = "spectrum-mosaic-pushbroom-worker"
    backend_image_repo: str = "spectrum-mosaic-backend"
    frontend_image_repo: str = "spectrum-mosaic-frontend"

    def cors_origin_list(self) -> list[str]:
        return [p.strip() for p in self.cors_origins.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
