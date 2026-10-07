import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from salesmind.core.config import Settings
from salesmind.db.session import Database
from salesmind.infrastructure.health import check_readiness
from salesmind.infrastructure.redis import RedisStore
from salesmind.main import create_app


def disabled_settings():
    return Settings(
        _env_file=None, sales_database_url=None, app_database_url=None, redis_url=None
    )


@pytest.mark.asyncio
async def test_readiness_with_real_sessions_and_redis_ping():
    database = Database(disabled_settings())
    database.sales_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    database.app_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    redis = RedisStore(disabled_settings())
    redis.client = Mock(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    try:
        result = await check_readiness(database, redis, timeout=2)
        assert result == {
            "status": "ready",
            "checks": {"sales_database": "ok", "app_database": "ok", "redis": "ok"},
        }
    finally:
        await redis.close()
        await database.dispose()
    redis.client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_failure_hides_underlying_credentials():
    database = Database(disabled_settings())
    database.sales_engine = Mock(
        connect=Mock(side_effect=SQLAlchemyError("mysql-secret"))
    )
    redis = RedisStore(disabled_settings())
    redis.client = Mock(ping=AsyncMock(side_effect=RedisError("redis-secret")))
    result = await check_readiness(database, redis, timeout=1)
    assert result == {
        "status": "not_ready",
        "checks": {
            "sales_database": "error",
            "app_database": "not_configured",
            "redis": "error",
        },
    }


@pytest.mark.asyncio
async def test_readiness_redis_timeout_is_bounded():
    redis = RedisStore(disabled_settings())

    async def slow_ping():
        await asyncio.Event().wait()

    redis.client = Mock(ping=slow_ping)
    result = await asyncio.wait_for(
        check_readiness(Database(disabled_settings()), redis, timeout=0.01), timeout=1
    )
    assert result["checks"]["redis"] == "timeout"


def test_ready_endpoint_returns_503_for_missing_configuration():
    with TestClient(create_app(disabled_settings())) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert set(response.json()["checks"].values()) == {"not_configured"}


def test_ready_endpoint_returns_200_when_dependencies_are_ready(monkeypatch):
    check = AsyncMock(return_value={"status": "ready", "checks": {}})
    monkeypatch.setattr("salesmind.main.check_readiness", check)
    with TestClient(create_app(disabled_settings())) as client:
        assert client.get("/health/ready").status_code == 200
    check.assert_awaited_once()


def test_shutdown_disposes_database_even_when_redis_close_fails(monkeypatch):
    dispose = AsyncMock()
    monkeypatch.setattr(Database, "dispose", dispose)
    monkeypatch.setattr(
        RedisStore, "close", AsyncMock(side_effect=RuntimeError("close"))
    )
    app = create_app(disabled_settings())
    with pytest.raises(RuntimeError, match="close"), TestClient(app):
        assert hasattr(app.state, "redis")
    dispose.assert_awaited_once()
    assert not hasattr(app.state, "redis")


@pytest.mark.asyncio
async def test_redis_client_decodes_url_and_owns_its_pool():
    redis = RedisStore(
        Settings(
            _env_file=None, redis_url="redis://:encoded%40password@localhost:6380/0"
        )
    )
    try:
        options = redis.client.connection_pool.connection_kwargs
        assert options["password"] == "encoded@password"
        assert options["port"] == 6380
        assert options["decode_responses"] is True
        assert redis.client.auto_close_connection_pool
    finally:
        await redis.close()
