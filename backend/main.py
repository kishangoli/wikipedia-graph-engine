"""Application setup and resource lifetime; HTTP handlers live in backend/api."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router
from backend.config import PROJECT_ROOT, Settings
from backend.services.cache import SQLiteCache
from backend.services.search import SearchManager
from backend.services.wikipedia import WikipediaClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cache = SQLiteCache(settings.cache_path, settings.cache_ttl_seconds)
        await cache.initialize()
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, headers={"User-Agent": settings.user_agent}, follow_redirects=False) as http:
            wikipedia = WikipediaClient(settings, cache, http)
            manager = SearchManager(settings, wikipedia)
            app.state.search_manager = manager
            try:
                yield
            finally:
                await manager.close()

    app = FastAPI(title="Wikipedia Path Finder", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "frontend"), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "frontend" / "index.html")

    return app


app = create_app()
