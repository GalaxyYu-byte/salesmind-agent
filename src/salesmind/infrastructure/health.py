"""只读连通性探测；对外不输出连接凭据或底层异常。"""

import asyncio

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from salesmind.db.session import Database
from salesmind.infrastructure.redis import RedisStore


async def _check_database(engine: AsyncEngine | None, timeout: float) -> str:
    if engine is None:
        return "not_configured"
    try:
        async with asyncio.timeout(timeout), engine.connect() as connection:
            return "ok" if await connection.scalar(text("SELECT 1")) == 1 else "error"
    except TimeoutError:
        return "timeout"
    except (SQLAlchemyError, OSError):
        return "error"


async def _check_redis(redis: RedisStore, timeout: float) -> str:
    if redis.client is None:
        return "not_configured"
    try:
        async with asyncio.timeout(timeout):
            return "ok" if await redis.client.ping() else "error"
    except TimeoutError:
        return "timeout"
    except (RedisError, OSError):
        return "error"


async def check_readiness(
    database: Database, redis: RedisStore, timeout: float
) -> dict:
    """同时检查两个 MySQL schema 和 Redis，总等待时间受探测超时约束。"""
    results = await asyncio.gather(
        _check_database(database.sales_engine, timeout),
        _check_database(database.app_engine, timeout),
        _check_redis(redis, timeout),
    )
    return {
        "status": "ready" if all(result == "ok" for result in results) else "not_ready",
        "checks": dict(
            zip(("sales_database", "app_database", "redis"), results, strict=True)
        ),
    }
