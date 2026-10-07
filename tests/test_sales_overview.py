"""真实 Session/SQL 聚合与 HTTP 边界测试；不访问开发 MySQL 或凭据。"""

import asyncio
from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from salesmind.api.auth import get_current_user
from salesmind.api.dependencies import get_sales_session
from salesmind.core.config import Settings
from salesmind.main import create_app
from salesmind.services.sales_overview import (
    OverviewQuery,
    SalesQueryError,
    query_sales_overview,
    resolve_months,
)


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        statements = [
            "ATTACH DATABASE ':memory:' AS sales_data",
            (
                "CREATE TABLE sales_data.dataset_version (id INTEGER PRIMARY KEY, "
                "version BIGINT, refreshed_at DATETIME, available_from DATE, complete_through DATE)"
            ),
            (
                "CREATE TABLE sales_data.sales_region (id INTEGER PRIMARY KEY, name TEXT, "
                "parent_id INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.product (id INTEGER PRIMARY KEY, sku TEXT, name TEXT, "
                "category TEXT, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.sales_order (id INTEGER PRIMARY KEY, region_id INTEGER, "
                "product_id INTEGER, amount NUMERIC(18,2), cost NUMERIC(18,2), quantity INTEGER, "
                "status TEXT, order_date DATE)"
            ),
            (
                "INSERT INTO sales_data.dataset_version VALUES "
                "(1, 2026092201, '2026-09-22', '2024-02-01', '2026-09-21')"
            ),
            (
                "INSERT INTO sales_data.sales_region VALUES "
                "(1, '华东', NULL, 1, '2026-01-01', '2026-01-01'),"
                "(2, '华南', NULL, 1, '2026-01-01', '2026-01-01'),"
                "(3, '空区域', NULL, 1, '2026-01-01', '2026-01-01')"
            ),
            (
                "INSERT INTO sales_data.product VALUES "
                "(1, 'P1', '商品', '数码', 1, '2026-01-01', '2026-01-01')"
            ),
        ]
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(
            text(
                "INSERT INTO sales_data.sales_order VALUES "
                "(1, 1, 1, 100, 60, 1, 'COMPLETED', '2025-08-10'),"
                "(2, 1, 1, 80, 50, 1, 'COMPLETED', '2026-07-31'),"
                "(3, 1, 1, 120, 70, 1, 'COMPLETED', '2026-08-01'),"
                "(4, 1, 1, 900, 500, 1, 'REFUNDED', '2026-08-12'),"
                "(5, 1, 1, 800, 400, 1, 'CANCELLED', '2026-08-13'),"
                "(6, 2, 1, 59.97, 30.01, 3, 'COMPLETED', '2026-08-31'),"
                "(7, 2, 1, 50, 70, 1, 'COMPLETED', '2026-09-01')"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_sql_aggregation_filters_status_dates_and_region(sessions):
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    async with sessions() as session:
        result = await query_sales_overview(session, query)
    assert result.source == "mysql"
    assert result.totals.sales_amount == "179.97"
    assert result.totals.gross_profit == "79.96"
    assert result.totals.order_count == 2
    assert result.totals.quantity == 4
    assert result.dataset.order_count == 7
    assert {c.month for c in result.dataset.cells} == {"2025-08", "2026-08"}
    async with sessions() as session:
        region = await query_sales_overview(
            session, query.model_copy(update={"region_id": 1})
        )
    assert region.totals.sales_amount == "120.00"
    assert all(cell.region == 1 for cell in region.dataset.cells)


@pytest.mark.asyncio
async def test_empty_scope_differs_from_unknown_region(sessions):
    async with sessions() as session:
        result = await query_sales_overview(
            session,
            OverviewQuery(start_month="2026-08", end_month="2026-08", region_id=3),
        )
        assert result.totals.sales_amount == "0.00"
        assert result.dataset.cells == []
        with pytest.raises(SalesQueryError) as raised:
            await query_sales_overview(session, OverviewQuery(region_id=999))
        assert raised.value.status_code == 404


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-09", "2026-09"),
        ("2024-01", "2024-01"),
        ("2026-08", "2026-03"),
        ("2024-02", "2026-08"),
        ("2026-08", None),
    ],
)
def test_rejects_partial_missing_reversed_and_unbounded_periods(start, end):
    with pytest.raises(SalesQueryError):
        resolve_months(
            OverviewQuery(start_month=start, end_month=end),
            date(2024, 2, 1),
            date(2026, 9, 21),
        )


def test_defaults_anchor_to_complete_months_and_skip_partial_first_month():
    assert resolve_months(OverviewQuery(), date(2024, 2, 1), date(2026, 9, 21)) == [
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert resolve_months(OverviewQuery(), date(2026, 6, 5), date(2026, 8, 31)) == [
        "2026-07",
        "2026-08",
    ]


@pytest.mark.asyncio
async def test_http_query_response_validation_after_authentication(sessions):
    settings = Settings(
        _env_file=None,
        sales_database_url=None,
        app_database_url=None,
        redis_url=None,
    )
    app = create_app(settings)

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_sales_session] = session_override
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1000)),
            base_url="http://localhost",
        ) as client:
            response = await client.get(
                "/api/v1/sales/overview?start_month=2026-08&end_month=2026-08&region_id=1"
            )
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["totals"]["sales_amount"] == "120.00"
            assert await session_count(client) == 422
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1000)),
            base_url="http://localhost",
        ) as external:
            assert (await external.get("/api/v1/sales/overview")).status_code == 200


async def session_count(client):
    return (
        await client.get("/api/v1/sales/overview?start_month=2026-13&end_month=2026-13")
    ).status_code


@pytest.mark.asyncio
async def test_sales_requires_login_by_default():
    app = create_app(
        Settings(
            _env_file=None,
            sales_database_url=None,
            app_database_url=None,
            redis_url=None,
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
            base_url="http://localhost",
        ) as client,
    ):
        assert (await client.get("/api/v1/sales/overview")).status_code == 401


@pytest.mark.asyncio
async def test_timeout_does_not_return_underlying_details(monkeypatch):
    async def slow_query(session, query):
        await asyncio.sleep(2)

    monkeypatch.setattr("salesmind.api.sales.query_sales_overview", slow_query)
    app = create_app(
        Settings(
            _env_file=None,
            sales_database_url=None,
            app_database_url=None,
            redis_url=None,
            sales_query_timeout=0.01,
        )
    )

    async def session_override():
        yield None

    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_sales_session] = session_override
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
            base_url="http://localhost",
        ) as client,
    ):
        response = await client.get("/api/v1/sales/overview")
        assert response.status_code == 504
        assert response.json()["detail"] == "销售查询超时，请稍后重试"
