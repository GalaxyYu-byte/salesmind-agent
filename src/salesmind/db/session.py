"""独立的异步 MySQL 连接池与每次调用独享的 Session。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from salesmind.core.config import Settings

SQL_MODE = (
    "STRICT_TRANS_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)


class DatabaseNotConfiguredError(RuntimeError):
    """服务未配置数据库，不能创建查询或写入 Session。"""


def _create_engine(dsn: str, settings: Settings, *, read_only: bool) -> AsyncEngine:
    """连接按需建立；每条新连接使用 UTF-8、UTC 和严格 SQL 模式。"""
    url = make_url(dsn).update_query_dict({"charset": "utf8mb4"})
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        connect_args={"connect_timeout": settings.db_connect_timeout},
        hide_parameters=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def initialize_connection(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET SESSION time_zone = '+00:00'")
            cursor.execute(f"SET SESSION sql_mode = '{SQL_MODE}'")
            if read_only:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
        finally:
            cursor.close()

    return engine


class Database:
    """持有两个池，不建表、不导入数据，也不在启动时连接 MySQL。"""

    def __init__(self, settings: Settings):
        self.sales_engine: AsyncEngine | None = None
        self.app_engine: AsyncEngine | None = None
        self._sales_sessions: async_sessionmaker[AsyncSession] | None = None
        self._app_sessions: async_sessionmaker[AsyncSession] | None = None
        if settings.database_enabled:
            self.sales_engine = _create_engine(
                settings.sales_database_url.get_secret_value(), settings, read_only=True
            )
            self.app_engine = _create_engine(
                settings.app_database_url.get_secret_value(), settings, read_only=False
            )
            self._sales_sessions = async_sessionmaker(
                self.sales_engine, expire_on_commit=False, autoflush=False
            )
            self._app_sessions = async_sessionmaker(
                self.app_engine, expire_on_commit=False, autoflush=False
            )

    @asynccontextmanager
    async def sales_session(self) -> AsyncIterator[AsyncSession]:
        """业务查询 Session，连接使用只读事务；离开时关闭并回滚未结束事务。"""
        if self._sales_sessions is None:
            raise DatabaseNotConfiguredError("数据库尚未配置")
        async with self._sales_sessions() as session:
            yield session

    @asynccontextmanager
    async def app_session(self) -> AsyncIterator[AsyncSession]:
        """应用 Session；调用方用 session.begin() 或 commit() 明确提交写入。"""
        if self._app_sessions is None:
            raise DatabaseNotConfiguredError("数据库尚未配置")
        async with self._app_sessions() as session:
            yield session

    async def dispose(self) -> None:
        """服务退出时释放两个池，即使第一个池释放失败仍尝试释放第二个。"""
        try:
            if self.sales_engine is not None:
                await self.sales_engine.dispose()
        finally:
            if self.app_engine is not None:
                await self.app_engine.dispose()
