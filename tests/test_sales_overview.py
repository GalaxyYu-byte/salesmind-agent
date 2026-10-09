"""真实 Session/SQL 聚合与 HTTP 边界测试；不访问开发 MySQL 或凭据。"""

import asyncio
import json
from datetime import date

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from salesmind.api.auth import get_current_user
from salesmind.api.dependencies import get_app_session, get_sales_session
from salesmind.core.config import Settings
from salesmind.db.entities.agent_app_users import AppUser
from salesmind.main import create_app
from salesmind.services.permissions import DataScope
from salesmind.services.query_cache import SalesQueryCache
from salesmind.services.sales_overview import (
    OverviewQuery,
    SalesQueryError,
    query_sales_overview,
    resolve_months,
)

FULL_METRICS = (
    "sales_amount",
    "order_count",
    "quantity",
    "gross_profit",
    "gross_margin",
    "avg_order_value",
    "product_share",
    "growth_rate",
    "sales_cost",
)


def scope(*, regions=None, reps=None, metrics=FULL_METRICS, version=1):
    return DataScope(
        user_id="test-user",
        allowed_region_ids=regions,
        allowed_rep_ids=reps,
        allowed_metrics=metrics,
        permission_version=version,
    )


def director(metrics=FULL_METRICS):
    return AppUser(
        id="test-user",
        username="director",
        display_name="测试总监",
        password_hash="unused",
        role="SALES_DIRECTOR",
        rep_id=None,
        allowed_metrics_json=list(metrics),
        permission_version=1,
        active=1,
    )


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        statements = [
            "ATTACH DATABASE ':memory:' AS sales_data",
            "ATTACH DATABASE ':memory:' AS agent_app",
            (
                "CREATE TABLE agent_app.app_user (id TEXT PRIMARY KEY, username TEXT, display_name TEXT, "
                "password_hash TEXT, role TEXT, rep_id INTEGER, allowed_metrics_json JSON, "
                "permission_version INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.dataset_version (id INTEGER PRIMARY KEY, "
                "version BIGINT, refreshed_at DATETIME, available_from DATE, complete_through DATE)"
            ),
            (
                "CREATE TABLE sales_data.sales_region (id INTEGER PRIMARY KEY, name TEXT, "
                "parent_id INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.sales_rep (id INTEGER PRIMARY KEY, name TEXT, "
                "region_id INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.product (id INTEGER PRIMARY KEY, sku TEXT, name TEXT, "
                "category TEXT, active INTEGER, created_at DATETIME, updated_at DATETIME)"
            ),
            (
                "CREATE TABLE sales_data.sales_order (id INTEGER PRIMARY KEY, region_id INTEGER, "
                "product_id INTEGER, rep_id INTEGER, amount NUMERIC(18,2), cost NUMERIC(18,2), quantity INTEGER, "
                "status TEXT, order_date DATE)"
            ),
            (
                "INSERT INTO sales_data.sales_rep VALUES "
                "(11, '销售员甲', 1, 1, '2026-01-01', '2026-01-01'),"
                "(12, '销售员乙', 1, 1, '2026-01-01', '2026-01-01'),"
                "(21, '销售员丙', 2, 1, '2026-01-01', '2026-01-01')"
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
                "INSERT INTO agent_app.app_user VALUES ('test-user','director','测试总监','unused',"
                "'SALES_DIRECTOR',NULL,:metrics,1,1,NULL,NULL)"
            ),
            {"metrics": json.dumps(FULL_METRICS)},
        )
        await connection.execute(
            text(
                "INSERT INTO sales_data.sales_order VALUES "
                "(1, 1, 1, 11, 100, 60, 1, 'COMPLETED', '2025-08-10'),"
                "(2, 1, 1, 12, 80, 50, 1, 'COMPLETED', '2026-07-31'),"
                "(3, 1, 1, 11, 120, 70, 1, 'COMPLETED', '2026-08-01'),"
                "(4, 1, 1, 12, 900, 500, 1, 'REFUNDED', '2026-08-12'),"
                "(5, 1, 1, 11, 800, 400, 1, 'CANCELLED', '2026-08-13'),"
                "(6, 2, 1, 21, 59.97, 30.01, 3, 'COMPLETED', '2026-08-31'),"
                "(7, 2, 1, 21, 50, 70, 1, 'COMPLETED', '2026-09-01')"
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
        result = await query_sales_overview(session, query, scope())
    assert result.source == "mysql"
    assert result.totals.sales_amount == "179.97"
    assert result.totals.gross_profit == "79.96"
    assert result.totals.order_count == 2
    assert result.totals.quantity == 4
    assert result.dataset.order_count == 7
    assert {c.month for c in result.dataset.cells} == {"2025-08", "2026-08"}
    async with sessions() as session:
        region = await query_sales_overview(
            session, query.model_copy(update={"region_id": 1}), scope()
        )
    assert region.totals.sales_amount == "120.00"
    assert all(cell.region == 1 for cell in region.dataset.cells)


@pytest.mark.asyncio
async def test_mom_baselines_use_equal_period_or_previous_month(sessions):
    async with sessions() as session:
        summary = await query_sales_overview(
            session,
            OverviewQuery(
                start_month="2026-07",
                end_month="2026-08",
                comparison="mom",
            ),
            scope(),
        )
        trend = await query_sales_overview(
            session,
            OverviewQuery(
                start_month="2026-07",
                end_month="2026-08",
                comparison="mom",
                baseline_mode="month",
            ),
            scope(),
        )
    assert summary.baseline_months == ["2026-05", "2026-06"]
    assert trend.baseline_months == ["2026-06", "2026-07"]
    assert summary.comparison == trend.comparison == "mom"
    assert summary.baseline_covered and trend.baseline_covered
    assert summary.totals.sales_amount == trend.totals.sales_amount == "259.97"


@pytest.mark.asyncio
async def test_history_uses_same_authorized_scope_and_never_invents_coverage(sessions):
    async with sessions() as session:
        data = await query_sales_overview(
            session,
            OverviewQuery(
                start_month="2026-08",
                end_month="2026-08",
                comparison="none",
                history_months=3,
            ),
            scope(regions=(1,), reps=(11,)),
        )
        denied = await query_sales_overview(
            session,
            OverviewQuery(
                start_month="2026-08",
                end_month="2026-08",
                comparison="mom",
                history_months=3,
            ),
            scope(metrics=("sales_amount",)),
        )
    assert data.history_months == ["2026-05", "2026-06", "2026-07"]
    assert data.baseline_months == [] and not data.baseline_covered
    assert all(c.region == 1 for c in data.dataset.cells)
    assert all(c.month == "2026-08" for c in denied.dataset.cells)
    assert denied.history_months == [] and not denied.baseline_covered


@pytest.mark.asyncio
async def test_cache_hit_expiry_refresh_and_data_publication(sessions):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = SalesQueryCache(redis, ttl_seconds=60)
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    calls = 0

    async def load(session, query, scope):
        nonlocal calls
        calls += 1
        return await query_sales_overview(session, query, scope)

    async def run(query=query):
        async with sessions() as session:
            return await cache.query(session, query, scope(), load)

    try:
        first, second = await run(), await run()
        assert calls == 1
        assert not first.cache.hit and second.cache.hit
        assert first.queried_at == second.queried_at
        async with sessions() as session:
            await session.execute(
                text("UPDATE sales_data.sales_order SET amount=121 WHERE id=3")
            )
            await session.commit()
        assert (await run()).totals.sales_amount == "179.97"
        fresh = await run(query.model_copy(update={"refresh": True}))
        assert fresh.totals.sales_amount == "180.97" and not fresh.cache.hit
        assert calls == 2
        key = (await redis.keys("salesmind:query:*"))[0]
        await redis.expire(key, 0)
        assert not (await run()).cache.hit and calls == 3
        async with sessions() as session:
            await session.execute(
                text("UPDATE sales_data.dataset_version SET version=version+1")
            )
            await session.commit()
        assert not (await run()).cache.hit and calls == 4
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_cache_isolates_scope_permission_conditions_and_coalesces(sessions):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = SalesQueryCache(redis)
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    calls = 0

    async def load(session, query, scope):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return await query_sales_overview(session, query, scope)

    async def run(data_scope=None, task_query=query):
        async with sessions() as session:
            return await cache.query(session, task_query, data_scope or scope(), load)

    try:
        results = await asyncio.gather(*(run() for _ in range(5)))
        assert calls == 1 and sum(r.cache.hit for r in results) == 4
        assert cache._locks == {}
        restricted = await run(
            scope(regions=(1,), reps=(11,), metrics=("order_count",))
        )
        assert restricted.totals.sales_amount is None
        assert restricted.totals.order_count == 1
        await run(scope(version=2))
        await run(DataScope("another-user", None, None, FULL_METRICS, 1))
        await run(task_query=query.model_copy(update={"comparison": "mom"}))
        await run(task_query=query.model_copy(update={"history_months": 3}))
        assert calls == 6
        with pytest.raises(SalesQueryError):
            await run(scope(regions=(1,)), query.model_copy(update={"region_id": 2}))
        assert calls == 6
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_cache_corruption_failure_oversize_and_disabled_fall_back(sessions):
    from unittest.mock import AsyncMock

    from redis.exceptions import ConnectionError

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    cache = SalesQueryCache(redis)
    calls = 0

    async def load(session, query, scope):
        nonlocal calls
        calls += 1
        return await query_sales_overview(session, query, scope)

    async def run(cache):
        async with sessions() as session:
            return await cache.query(session, query, scope(), load)

    try:
        await run(cache)
        key = (await redis.keys("salesmind:query:*"))[0]
        await redis.set(key, "{broken", ex=60)
        assert (await run(cache)).totals.sales_amount == "179.97"
        broken = AsyncMock()
        broken.get.side_effect = ConnectionError("private connection detail")
        broken.set.side_effect = ConnectionError("private connection detail")
        assert (await run(SalesQueryCache(broken))).cache is None
        await redis.flushdb()
        assert (await run(SalesQueryCache(redis, max_bytes=10))).cache is None
        assert await redis.keys("salesmind:query:*") == []
        await run(SalesQueryCache(redis, ttl_seconds=0))
        await run(SalesQueryCache(None))
        assert calls == 6
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_http_cache_still_reloads_permissions_and_force_refreshes(
    sessions, monkeypatch
):
    app = create_app(
        Settings(
            _env_file=None,
            sales_database_url=None,
            app_database_url=None,
            redis_url=None,
        )
    )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    calls = 0

    async def session_override():
        async with sessions() as session:
            yield session

    async def load(session, query, scope):
        nonlocal calls
        calls += 1
        return await query_sales_overview(session, query, scope)

    async def current_user():
        async with sessions() as db:
            return await db.get(AppUser, "test-user")

    monkeypatch.setattr("salesmind.api.sales.query_sales_overview", load)
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_app_session] = session_override
    app.dependency_overrides[get_sales_session] = session_override
    try:
        async with app.router.lifespan_context(app):
            app.state.query_cache = SalesQueryCache(redis)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                url = "/api/v1/sales/overview?start_month=2026-08&end_month=2026-08"
                first = await client.get(url)
                second = await client.get(url)
                assert first.status_code == second.status_code == 200
                assert (
                    not first.json()["cache"]["hit"] and second.json()["cache"]["hit"]
                )
                assert second.headers["cache-control"] == "no-store"
                assert calls == 1
                refreshed = await client.get(url + "&refresh=true")
                assert (
                    refreshed.status_code == 200
                    and not refreshed.json()["cache"]["hit"]
                )
                assert calls == 2
                async with sessions() as db:
                    await db.execute(
                        text(
                            "UPDATE agent_app.app_user SET allowed_metrics_json=:metrics, permission_version=2 WHERE id='test-user'"
                        ),
                        {"metrics": json.dumps(["order_count"])},
                    )
                    await db.commit()
                limited = await client.get(url)
                assert limited.status_code == 200 and calls == 3
                assert limited.json()["totals"] == {"order_count": 2}
                assert all(
                    c.get("amount") is None for c in limited.json()["dataset"]["cells"]
                )
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_cancelled_or_failed_queries_do_not_poison_cache_or_lock(sessions):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = SalesQueryCache(redis)
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    started = asyncio.Event()

    async def blocked(session, query, scope):
        started.set()
        await asyncio.Event().wait()

    async def run(loader):
        async with sessions() as db:
            return await cache.query(db, query, scope(), loader)

    async def failing(session, query, scope):
        raise SalesQueryError("临时查询失败", 503)

    try:
        task = asyncio.create_task(run(blocked))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cache._locks == {} and await redis.keys("salesmind:query:*") == []
        with pytest.raises(SalesQueryError):
            await run(failing)
        assert cache._locks == {} and await redis.keys("salesmind:query:*") == []
        assert not (await run(query_sales_overview)).cache.hit
        assert (await run(query_sales_overview)).cache.hit
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_slow_redis_uses_bounded_wait_and_skips_retry(sessions):
    from unittest.mock import AsyncMock

    redis = AsyncMock()
    cache = SalesQueryCache(redis, io_timeout=0.01)

    async def slow_get(key):
        await asyncio.Event().wait()

    redis.get.side_effect = slow_get
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    async with asyncio.timeout(1), sessions() as session:
        result = await cache.query(session, query, scope(), query_sales_overview)
    assert result.totals.sales_amount == "179.97" and result.cache is None
    assert redis.get.await_count == 1
    redis.ttl.assert_not_awaited()
    redis.set.assert_not_awaited()
    assert cache._locks == {}


@pytest.mark.asyncio
async def test_empty_scope_differs_from_unknown_region(sessions):
    async with sessions() as session:
        result = await query_sales_overview(
            session,
            OverviewQuery(start_month="2026-08", end_month="2026-08", region_id=3),
            scope(),
        )
        assert result.totals.sales_amount == "0.00"
        assert result.dataset.cells == []
        with pytest.raises(SalesQueryError) as raised:
            await query_sales_overview(session, OverviewQuery(region_id=999), scope())
        assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_rep_only_sees_own_orders_and_same_baseline_scope(sessions):
    async with sessions() as session:
        # 同区域另一个销售员的当前期和基期都必须被排除。
        await session.execute(
            text(
                "INSERT INTO sales_data.sales_order VALUES "
                "(8, 1, 1, 12, 777, 333, 9, 'COMPLETED', '2025-08-12'),"
                "(9, 1, 1, 12, 999, 555, 8, 'COMPLETED', '2026-08-12')"
            )
        )
        own = scope(regions=(1,), reps=(11,))
        result = await query_sales_overview(
            session,
            OverviewQuery(start_month="2026-08", end_month="2026-08"),
            own,
        )
        assert result.totals.sales_amount == "120.00"
        assert result.totals.gross_profit == "50.00"
        assert result.totals.order_count == 1
        assert result.dataset.order_count == 3
        assert [(c.month, c.amount) for c in result.dataset.cells] == [
            ("2025-08", "10000"),
            ("2026-08", "12000"),
        ]
        assert [r.id for r in result.dataset.regions] == [1]
        with pytest.raises(SalesQueryError) as denied:
            await query_sales_overview(
                session,
                OverviewQuery(start_month="2026-08", end_month="2026-08", region_id=2),
                own,
            )
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_manager_intersects_explicit_filter_with_multiple_grants(sessions):
    query = OverviewQuery(start_month="2026-08", end_month="2026-08")
    async with sessions() as session:
        manager = scope(regions=(1, 2))
        result = await query_sales_overview(session, query, manager)
        assert result.totals.sales_amount == "179.97"
        assert [r.id for r in result.dataset.regions] == [1, 2]
        filtered = await query_sales_overview(
            session,
            query.model_copy(update={"region_id": 2}),
            manager,
        )
        assert filtered.totals.sales_amount == "59.97"
        assert all(cell.region == 2 for cell in filtered.dataset.cells)
        assert {cell.month for cell in filtered.dataset.cells} == {"2026-08"}
        with pytest.raises(SalesQueryError) as denied:
            await query_sales_overview(
                session,
                query.model_copy(update={"region_id": 3}),
                manager,
            )
        assert denied.value.status_code == 403
        limited = await query_sales_overview(session, query, scope(regions=(1, 3)))
        assert limited.totals.sales_amount == "120.00"
        assert limited.dataset.order_count == 5


@pytest.mark.asyncio
async def test_director_global_scope_still_redacts_restricted_metrics(sessions):
    allowed = scope(metrics=("sales_amount", "order_count"))
    async with sessions() as session:
        result = await query_sales_overview(
            session,
            OverviewQuery(start_month="2026-08", end_month="2026-08"),
            allowed,
        )
    assert result.totals.sales_amount == "179.97"
    assert result.totals.order_count == 2
    assert result.totals.gross_profit is None
    assert result.totals.quantity is None
    assert result.baseline_covered is False
    assert {c.month for c in result.dataset.cells} == {"2026-08"}
    assert all(
        c.cost is None and c.gross_profit is None and c.quantity is None
        for c in result.dataset.cells
    )
    assert result.access.user_id == allowed.user_id
    assert result.access.permission_version == allowed.permission_version
    assert result.access.scope_hash == allowed.scope_hash
    assert set(result.access.allowed_metrics) == set(allowed.allowed_metrics)
    assert result.access.scope_label


@pytest.mark.asyncio
async def test_profit_without_cost_permission_exposes_only_profit(sessions):
    async with sessions() as session:
        result = await query_sales_overview(
            session,
            OverviewQuery(start_month="2026-08", end_month="2026-08"),
            scope(metrics=("gross_profit",)),
        )
    assert result.totals.gross_profit == "79.96"
    assert result.totals.sales_amount is None
    assert result.totals.order_count is None
    assert result.dataset.order_count is None
    assert all(
        c.amount is None and c.cost is None and c.count is None
        for c in result.dataset.cells
    )
    assert (
        sum(int(c.gross_profit) for c in result.dataset.cells if c.month == "2026-08")
        == 7996
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [scope(regions=()), scope(metrics=())])
async def test_empty_permission_rejects_query_before_database_access(restricted):
    with pytest.raises(HTTPException) as denied:
        await query_sales_overview(None, OverviewQuery(), restricted)
    assert denied.value.status_code == 403


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

    app.dependency_overrides[get_current_user] = lambda: director()
    app.dependency_overrides[get_app_session] = session_override
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


@pytest.mark.asyncio
async def test_http_omits_unauthorized_fields(sessions):
    app = create_app(
        Settings(
            _env_file=None,
            sales_database_url=None,
            app_database_url=None,
            redis_url=None,
        )
    )

    async def session_override():
        async with sessions() as session:
            yield session

    async with sessions() as session:
        await session.execute(
            text(
                "UPDATE agent_app.app_user SET allowed_metrics_json = :metrics WHERE id = 'test-user'"
            ),
            {"metrics": json.dumps(["order_count"])},
        )
        await session.commit()

    app.dependency_overrides[get_current_user] = lambda: director(("order_count",))
    app.dependency_overrides[get_app_session] = session_override
    app.dependency_overrides[get_sales_session] = session_override
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client,
    ):
        response = await client.get(
            "/api/v1/sales/overview?start_month=2026-08&end_month=2026-08"
        )
    assert response.status_code == 200
    body = response.json()
    assert body["totals"] == {"order_count": 2}
    assert all(
        not {"amount", "cost", "gross_profit", "quantity"} & cell.keys()
        for cell in body["dataset"]["cells"]
    )
    assert body["access"]["allowed_metrics"] == ["order_count"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("allowed_metrics_json='[]'", 409),
        ("permission_version=2", 409),
        ("active=0", 403),
    ],
)
async def test_http_rechecks_permissions_before_returning_query_result(
    sessions, monkeypatch, change, expected
):
    app = create_app(
        Settings(
            _env_file=None,
            sales_database_url=None,
            app_database_url=None,
            redis_url=None,
        )
    )

    async def session_override():
        async with sessions() as session:
            yield session

    async def query_and_revoke(session, query, data_scope):
        result = await query_sales_overview(session, query, data_scope)
        await session.execute(
            text("UPDATE agent_app.app_user SET " + change + " WHERE id='test-user'")
        )
        await session.commit()
        return result

    monkeypatch.setattr("salesmind.api.sales.query_sales_overview", query_and_revoke)
    app.dependency_overrides[get_current_user] = lambda: director()
    app.dependency_overrides[get_app_session] = session_override
    app.dependency_overrides[get_sales_session] = session_override
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client,
    ):
        response = await client.get(
            "/api/v1/sales/overview?start_month=2026-08&end_month=2026-08"
        )
    assert response.status_code == expected
    assert "totals" not in response.json()


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
    async def slow_query(session, query, scope):
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

    app.dependency_overrides[get_current_user] = lambda: director()
    app.dependency_overrides[get_app_session] = session_override
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
