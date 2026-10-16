from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import bootstrap, health_payload
from app.config import get_settings
from app.db import SessionLocal
from app.routers import audit, auth, docs, jobs, profiles, rbac, system, tools, users
from app.schemas import HealthOut
from app.services.progress_ws import start_consumers, websocket_job
from app.services.stage_steps import start_stage_step_sync

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        db = SessionLocal()
        try:
            bootstrap(db, settings)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        log.error("bootstrap failed, API still serving: %s", exc)
    loop = asyncio.get_running_loop()
    try:
        start_consumers(loop)
    except Exception:  # noqa: BLE001
        pass
    try:
        start_stage_step_sync()
    except Exception:  # noqa: BLE001
        log.exception("stage step sync failed to start")
    yield


app = FastAPI(title="ms_mosaic server", version="1.0.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(jobs.router)
app.include_router(docs.router)
app.include_router(tools.router)
app.include_router(system.router)
app.include_router(users.router)
app.include_router(rbac.router)
app.include_router(audit.router)


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    db = SessionLocal()
    try:
        return HealthOut(**health_payload(db, get_settings()))
    finally:
        db.close()


@app.websocket("/ws/jobs/{job_id}")
async def ws_jobs(websocket: WebSocket, job_id: str) -> None:
    await websocket_job(websocket, job_id)
