import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from salesmind.api.dependencies import AppSession, SalesSession
from salesmind.core.config import Settings
from salesmind.db.session import Database, DatabaseNotConfiguredError
from salesmind.main import create_app


def configured_settings():
    return Settings(
        _env_file=None,
        sales_database_url=(
            "mysql+asyncmy://sales_reader:secret%40value@127.0.0.1:3306/sales_data"
        ),
        app_database_url=(
            "mysql+asyncmy://agent_writer:other_secret@127.0.0.1:3306/agent_app"
        ),
    )


@pytest.fixture
def sqlite_database(monkeypatch):
    """替换物理连接，保留真实 Session 和事务行为。"""

    def create_sqlite_engine(dsn, settings, *, read_only):
        return create_async_engine("sqlite+aiosqlite:///:memory:")

    monkeypatch.setattr("salesmind.db.session._create_engine", create_sqlite_engine)
    return Database(configured_settings())


@pytest.mark.asyncio
async def test_mysql_engines_are_lazy_and_initialize_each_new_connection():
    database = Database(configured_settings())
    try:
        assert database.sales_engine is not database.app_engine
        assert database.sales_engine.url.database == "sales_data"
        assert database.app_engine.url.database == "agent_app"
        assert database.sales_engine.url.password == "secret@value"
        for engine, read_only in (
            (database.sales_engine, True),
            (database.app_engine, False),
        ):
            assert engine.pool.checkedout() == 0
            connection = Mock()
            cursor = connection.cursor.return_value
            # 最后一个监听器为本项目的初始化逻辑；不运行驱动自己的握手。
            initialize = list(engine.sync_engine.pool.dispatch.connect)[-1]
            initialize(connection, Mock())
            statements = [call.args[0] for call in cursor.execute.call_args_list]
            assert "SET SESSION time_zone = '+00:00'" in statements
            assert any("STRICT_TRANS_TABLES" in statement for statement in statements)
            assert ("SET SESSION TRANSACTION READ ONLY" in statements) is read_only
            cursor.close.assert_called_once()
            cursor.reset_mock()
            cursor.execute.side_effect = RuntimeError("initialization failed")
            with pytest.raises(RuntimeError):
                initialize(connection, Mock())
            cursor.close.assert_called_once()
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_unconfigured_database_cannot_provide_sessions():
    database = Database(
        Settings(_env_file=None, sales_database_url=None, app_database_url=None)
    )
    for session_context in (database.sales_session, database.app_session):
        with pytest.raises(DatabaseNotConfiguredError):
            async with session_context():
                pytest.fail("unconfigured database yielded a session")
    await database.dispose()


@pytest.mark.asyncio
async def test_app_transaction_commit_rollback_and_pool_isolation(sqlite_database):
    database = sqlite_database
    try:
        async with database.app_engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE records (id INTEGER PRIMARY KEY)")
            )

        async with database.app_session() as session, session.begin():
            await session.execute(text("INSERT INTO records VALUES (1)"))

        with pytest.raises(RuntimeError, match="abort"):
            async with database.app_session() as session, session.begin():
                await session.execute(text("INSERT INTO records VALUES (2)"))
                raise RuntimeError("abort")

        # 没有显式提交的写入在上下文退出后被回滚。
        async with database.app_session() as session:
            await session.execute(text("INSERT INTO records VALUES (3)"))

        async with database.app_session() as session:
            assert (await session.scalars(text("SELECT id FROM records"))).all() == [1]

        async with database.sales_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'records'")
                )
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_calls_get_independent_sessions_and_close_on_cancel(
    sqlite_database,
):
    database = sqlite_database
    sessions = []
    entered = asyncio.Event()

    async def hold_session():
        async with database.sales_session() as session:
            sessions.append(session)
            await session.execute(text("SELECT 1"))
            entered.set()
            await asyncio.Event().wait()

    try:
        task = asyncio.create_task(hold_session())
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with database.sales_session() as other:
            assert other is not sessions[0]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not sessions[0].in_transaction()
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_dispose_attempts_both_pools_after_failure():
    database = Database(
        Settings(_env_file=None, sales_database_url=None, app_database_url=None)
    )
    database.sales_engine = Mock(dispose=AsyncMock(side_effect=RuntimeError("failure")))
    database.app_engine = Mock(dispose=AsyncMock())
    with pytest.raises(RuntimeError):
        await database.dispose()
    database.app_engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("configured", [True, False])
def test_lifespan_keeps_liveness_independent_of_database_and_disposes(
    monkeypatch, configured
):
    settings = (
        configured_settings()
        if configured
        else Settings(_env_file=None, sales_database_url=None, app_database_url=None)
    )
    dispose = AsyncMock()
    monkeypatch.setattr(Database, "dispose", dispose)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert app.state.settings is settings
        assert (app.state.database.sales_engine is not None) is configured
    dispose.assert_awaited_once()
    assert not hasattr(app.state, "database")


def test_api_returns_503_when_database_is_not_configured():
    app = create_app(
        Settings(_env_file=None, sales_database_url=None, app_database_url=None)
    )

    @app.get("/test-sales")
    async def sales_route(session: SalesSession):
        return {"unexpected": True}

    @app.get("/test-app")
    async def app_route(session: AppSession):
        return {"unexpected": True}

    with TestClient(app) as client:
        for path in ("/test-sales", "/test-app"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "数据库尚未配置"}
