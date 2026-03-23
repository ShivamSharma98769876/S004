import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware.request_logging import RequestLoggingMiddleware

from app.api import (
    routes_admin,
    routes_analytics,
    routes_auth,
    routes_dashboard,
    routes_health,
    routes_landing,
    routes_marketplace,
    routes_metrics,
    routes_settings,
    routes_trades,
)
from app.db_client import close_db_pool, init_db_pool
from app.services.position_monitor import position_monitor_loop
from app.services.trades_service import run_auto_execute_cycle


async def _auto_execute_loop() -> None:
    """Background task: run auto-execute every 30 seconds for users with engine_running=true."""
    while True:
        try:
            await run_auto_execute_cycle()
        except Exception:
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db_pool()
    auto_task = asyncio.create_task(_auto_execute_loop())
    monitor_task = asyncio.create_task(position_monitor_loop())
    try:
        yield
    finally:
        auto_task.cancel()
        monitor_task.cancel()
        for t in (auto_task, monitor_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await close_db_pool()


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(title="S004 Backend", version="0.1.0", lifespan=lifespan)

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_metrics.router)
    app.include_router(routes_health.router, prefix="/api")
    app.include_router(routes_auth.router, prefix="/api")
    app.include_router(routes_admin.router, prefix="/api")
    app.include_router(routes_marketplace.router, prefix="/api")
    app.include_router(routes_analytics.router, prefix="/api")
    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(routes_trades.router, prefix="/api")
    app.include_router(routes_dashboard.router, prefix="/api")
    app.include_router(routes_landing.router, prefix="/api")
    return app


app = create_app()

