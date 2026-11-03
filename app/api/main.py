from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi import FastAPI

from app.api.routes import router
from app.config import PHOTOS_DIR, settings
from app.db import init_db
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Автомобильные торги", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    settings.ensure_dirs()
    init_db()
    from app.analysis.store import init_store

    init_store(recover=True)
    from app.vehicle_checks.store import init_store as init_vehicle_checks

    init_vehicle_checks(recover=True)
    from app.db import add_event

    add_event("system", "Сервис запущен")
    from app.email_worker import start_email_workers
    from app.scrape_worker import start_scrape_worker

    start_email_workers()
    start_scrape_worker()
    app.include_router(router, prefix="/api")
    # Never expose the SQLite DB, email artifacts, logs or snapshots as static files.
    app.mount("/data/photos", StaticFiles(directory=str(PHOTOS_DIR)), name="photos")
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
