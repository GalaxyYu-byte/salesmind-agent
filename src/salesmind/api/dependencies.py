"""路由使用的数据库 Session；事务提交由业务代码明确控制。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from salesmind.db.session import Database, DatabaseNotConfiguredError


def get_database(request: Request) -> Database:
    """取得 lifespan 创建的数据库资源。"""
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库资源尚未初始化",
        )
    return database


async def get_sales_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """为业务查询分配独立只读 Session。"""
    try:
        async with database.sales_session() as session:
            yield session
    except DatabaseNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库尚未配置",
        ) from None


async def get_app_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """为应用记录分配独立 Session，返回响应前关闭。"""
    try:
        async with database.app_session() as session:
            yield session
    except DatabaseNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库尚未配置",
        ) from None


SalesSession = Annotated[AsyncSession, Depends(get_sales_session, scope="function")]
AppSession = Annotated[AsyncSession, Depends(get_app_session, scope="function")]


def get_redis_client(request: Request) -> Redis:
    """共享连接池客户端；请求不单独关闭连接池。"""
    store = getattr(request.app.state, "redis", None)
    if store is None or store.client is None:
        raise HTTPException(status_code=503, detail="Redis 尚未配置或初始化")
    return store.client


RedisClient = Annotated[Redis, Depends(get_redis_client)]
