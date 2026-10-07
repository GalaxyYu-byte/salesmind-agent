from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from salesmind.api.auth import router as auth_router
from salesmind.api.sales import router as sales_router
from salesmind.core.config import Settings, get_settings
from salesmind.db.session import Database
from salesmind.infrastructure.health import check_readiness
from salesmind.infrastructure.redis import RedisStore


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂，配置与连接池归当前应用生命周期管理。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings if settings is not None else get_settings()
        app.title = app.state.settings.app_name
        async with AsyncExitStack() as resources:
            database = Database(app.state.settings)
            resources.push_async_callback(database.dispose)
            redis = RedisStore(app.state.settings)
            resources.push_async_callback(redis.close)
            app.state.database = database
            app.state.redis = redis
            try:
                yield
            finally:
                del app.state.database
                del app.state.redis

    app = FastAPI(
        title=settings.app_name if settings is not None else "SalesMind Agent",
        lifespan=lifespan,
    )
    app.include_router(sales_router)
    app.include_router(auth_router)

    @app.middleware("http")
    async def auth_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/auth", "/api/v1/sales")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RedisError)
    @app.exception_handler(SQLAlchemyError)
    async def resource_error(request: Request, error: Exception):
        return JSONResponse(
            {"detail": "服务连接暂时不可用，请稍后重试"}, status_code=503
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/v1/auth"):
            return JSONResponse(
                {"detail": "请求参数格式错误，请检查账号与验证码"}, status_code=422
            )
        return await request_validation_exception_handler(request, error)

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(request: Request):
        database = getattr(request.app.state, "database", None)
        redis = getattr(request.app.state, "redis", None)
        if database is None or redis is None:
            return JSONResponse({"status": "not_initialized"}, status_code=503)
        result = await check_readiness(
            database, redis, request.app.state.settings.health_check_timeout
        )
        return JSONResponse(
            result, status_code=200 if result["status"] == "ready" else 503
        )

    return app


app = create_app()
