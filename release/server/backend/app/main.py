from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import bootstrap
from app.config import get_settings
from app.db import SessionLocal
from app.routers import auth, customers, deliveries, licenses, manual, releases, settings_config, settings_notes
from app.schemas import HealthOut

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    migration = Path("/db/migrations/001_init.sql")
    if not migration.exists():
        migration = Path(__file__).resolve().parents[2] / "db" / "migrations" / "001_init.sql"
    db = SessionLocal()
    try:
        bootstrap(db, settings, migration)
    except Exception as exc:  # noqa: BLE001
        log.error("bootstrap failed: %s", exc)
    finally:
        db.close()
    Path(settings.keys_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.artifacts_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="spectrum-mosaic release console", version="1.0.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(releases.router)
app.include_router(licenses.public_router)
app.include_router(licenses.router)
app.include_router(deliveries.router)
app.include_router(manual.router)
app.include_router(settings_notes.router)
app.include_router(settings_config.router)


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", product=settings.product_name)
