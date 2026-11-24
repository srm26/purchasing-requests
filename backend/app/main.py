"""FastAPI entry point: serves /api/* and the built React app from one App Service (no CORS needed)."""
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select

from .config import get_settings
from .db import SessionLocal, init_db
from .models import LookupValue
from .routers import meta, requests
from .services import backup, lookups, storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("po_intake")
settings = get_settings()


def _refresh_job() -> None:
    with SessionLocal() as db:
        lookups.refresh(db, "scheduler")


def _backup_job() -> None:
    try:
        backup.backup_database()
    except Exception:
        log.exception("Nightly backup failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        if not db.scalar(select(func.count()).select_from(LookupValue)):
            lookups.refresh(db, "startup")
    try:
        if copied := storage.copy_local_to_blob():
            log.info("Copied %d local file(s) to Blob", copied)
    except Exception:
        log.exception("Copying local files to Blob failed; files saved before Blob was on may not open")
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(_refresh_job, "cron", hour=settings.nightly_hour_utc, minute=0, id="lookups")
        scheduler.add_job(_backup_job, "cron", hour=settings.nightly_hour_utc, minute=30, id="backup")
        scheduler.start()
    log.info("Started (env=%s, demo=%s, auth=%s, email=%s, sharepoint=%s, blob=%s)", settings.app_env, settings.demo_mode, settings.auth_mode,
             settings.email_configured, settings.sharepoint_configured, bool(settings.blob_account_url))
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="GES Purchasing Request Portal", lifespan=lifespan,
              docs_url="/api/docs" if settings.is_local else None, redoc_url=None, openapi_url="/api/openapi.json" if settings.is_local else None)
app.include_router(meta.router)
app.include_router(requests.router)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Full details go to the log; the browser only gets a reference to quote to support.
    ref = uuid.uuid4().hex[:8]
    log.exception("Unhandled error ref=%s on %s %s", ref, request.method, request.url.path)
    return JSONResponse({"detail": f"Something went wrong. Reference: {ref}"}, status_code=500)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- React SPA -------------------------------------------------------------------------------
STATIC = Path(settings.static_dir)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    file = (STATIC / path).resolve()
    if path and file.is_file() and STATIC.resolve() in file.parents:
        # Vite fingerprints files in /assets, so they can be cached forever.
        cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return FileResponse(file, headers={"Cache-Control": cache})
    index = STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(404, "Frontend not built. Run `npm run build` in frontend/.")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
