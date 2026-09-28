"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from trades import __version__
from trades.advisor import Recommender
from trades.api.security import DEV_ORIGINS, LocalGuard
from trades.arena.run import RunManager
from trades.config import SettingsStore, trades_home
from trades.data.base import DataError, ProviderNotConfigured, SymbolNotFound
from trades.data.service import DataService
from trades.live.service import LiveService
from trades.sim.session import SessionStore, SimError

log = logging.getLogger(__name__)

WEB_DIST = Path(os.environ.get("TRADES_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))


@dataclass
class AppContext:
    settings: SettingsStore
    data: DataService
    recommender: Recommender
    live: LiveService
    sims: SessionStore
    runs: RunManager


def create_app(
    settings_path: Path | None = None, *, start_live: bool = True, web_dist: Path | None = None
) -> FastAPI:
    home = settings_path.parent if settings_path else trades_home()
    store = SettingsStore(settings_path)
    data = DataService(store)
    recommender = Recommender()
    live = LiveService(data, store, recommender)
    sims = SessionStore(history_path=home / "sim_history.jsonl")
    runs = RunManager()
    ctx = AppContext(store, data, recommender, live, sims, runs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_live:
            await live.start()
        yield
        await runs.shutdown()
        await live.stop()

    app = FastAPI(
        title="Trades",
        version=__version__,
        lifespan=lifespan,
        description="Quant strategy research, live recommendations (data only) and a trading simulator.",
    )
    app.state.ctx = ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(DEV_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(LocalGuard)  # outermost: host allow-list + cross-site request check

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(SimError)
    async def _sim_error(_: Request, exc: SimError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ProviderNotConfigured)
    async def _not_configured(_: Request, exc: ProviderNotConfigured):
        return JSONResponse({"detail": str(exc), "code": "provider_not_configured"}, status_code=400)

    @app.exception_handler(SymbolNotFound)
    async def _not_found(_: Request, exc: SymbolNotFound):
        return JSONResponse({"detail": str(exc), "code": "symbol_not_found"}, status_code=404)

    @app.exception_handler(DataError)
    async def _data_error(_: Request, exc: DataError):
        return JSONResponse({"detail": str(exc), "code": "data_error"}, status_code=502)

    from trades.api import arena_routes  # noqa: PLC0415
    from trades.api.routes import router, ws_router  # noqa: PLC0415

    app.include_router(router, prefix="/api")
    app.include_router(arena_routes.router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(arena_routes.ws_router)

    dist = web_dist or WEB_DIST
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return HTMLResponse(
                "<h1>Trades API is running</h1><p>The web interface has not been built yet. Run "
                "<code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>, then reload. "
                "API docs: <a href='/docs'>/docs</a>.</p>"
            )

    return app
