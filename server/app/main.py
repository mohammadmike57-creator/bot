"""FastAPI application entry point.

Run locally:   uvicorn app.main:app --reload --port 8000   (from ./server)
Render:        uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import account, auth, health, kill_switch, news, performance, portfolio, signals, trades
from app.api import settings as settings_api
from app.config.settings import Settings, get_settings
from app.database.database import Database
from app.monitoring.logger import configure_logging, get_logger, request_id_var
from app.services.container import Container

log = get_logger(__name__)

AUTH_PATHS = {"/v1/auth"}


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container
        if c is None:
            # Schema is managed by Alembic: run `alembic upgrade head` (setup_mac.sh, Docker and
            # Render's pre-deploy step do this automatically).
            c = Container(settings, Database(settings.database_url))
        app.state.container = c
        c.startup()
        log.info("backend started", extra={"mode": settings.effective_trading_mode.value})
        yield

    app = FastAPI(title="Trading AI Platform", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.is_production() else "/docs", redoc_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def observability(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        c: Container | None = getattr(request.app.state, "container", None)
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        request_id_var.set(rid)
        if c is not None and request.url.path != "/health":
            client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or \
                (request.client.host if request.client else "unknown")
            is_auth = request.url.path in AUTH_PATHS
            limit = settings.rate_limit_auth_per_minute if is_auth else settings.rate_limit_per_minute
            if not c.rate_limiter.allow(f"{client}:{'auth' if is_auth else 'api'}", limit):
                c.metrics.inc("rate_limited")
                return JSONResponse({"detail": "RATE_LIMITED"}, status_code=429, headers={"Retry-After": "60"})
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": request.url.path})
            if c is not None:
                c.metrics.inc("http_errors")
            return JSONResponse({"detail": "INTERNAL_ERROR"}, status_code=500)
        elapsed = time.perf_counter() - start
        if c is not None:
            c.metrics.inc("http_requests")
            c.metrics.observe("api_latency", elapsed)
            if response.status_code >= 500:
                c.metrics.inc("http_errors")
        response.headers["X-Request-Id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        log.info("request", extra={"path": request.url.path, "method": request.method,
                                   "status": response.status_code, "ms": round(elapsed * 1000, 1)})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse({"detail": "VALIDATION_ERROR", "errors": errors}, status_code=422)

    for module in (health, auth, signals, account, trades, portfolio, news, performance, settings_api, kill_switch):
        app.include_router(module.router)
    return app


app = create_app()
