"""权限默认拒绝、跨库身份绑定和分析指标依赖；仅使用隔离 SQLite。"""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from salesmind.db.entities.agent_app_users import AppUser
from salesmind.services.conversation_schemas import AnalysisScope, AnalysisSpec
from salesmind.services.permissions import DataScope, load_data_scope


def user(**changes):
    values = {
        "id": "permission-user",
        "username": "permission-test",
        "display_name": "权限测试账号",
        "password_hash": "unused",
        "role": "SALES_DIRECTOR",
        "rep_id": None,
        "allowed_metrics_json": ["sales_amount", "order_count"],
        "permission_version": 1,
        "active": 1,
    }
    values.update(changes)
    return AppUser(**values)


def scope(**changes):
    values = {
        "user_id": "permission-user",
        "allowed_region_ids": None,
        "allowed_rep_ids": None,
        "allowed_metrics": ("sales_amount", "order_count"),
        "permission_version": 1,
    }
    values.update(changes)
    return DataScope(**values)


def spec(**changes):
    values = {"scope": AnalysisScope(months=["2026-08"]), "metric": "amount"}
    values.update(changes)
    return AnalysisSpec(**values)


@pytest_asyncio.fixture
async def permission_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        statements = [
            "ATTACH DATABASE ':memory:' AS agent_app",
            "ATTACH DATABASE ':memory:' AS sales_data",
            "CREATE TABLE agent_app.user_region_scope (user_id TEXT, region_id INTEGER, created_at DATETIME)",
            "CREATE TABLE sales_data.sales_region (id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)",
            "CREATE TABLE sales_data.sales_rep (id INTEGER PRIMARY KEY, name TEXT, region_id INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)",
            "INSERT INTO sales_data.sales_region VALUES (1, '华东', NULL, 1, NULL, NULL), (2, '华南', NULL, 1, NULL, NULL), (3, '停用区域', NULL, 0, NULL, NULL)",
            "INSERT INTO sales_data.sales_rep VALUES (11, '销售员甲', 1, 1, NULL, NULL), (12, '停用销售员', 1, 0, NULL, NULL), (13, '停用区域销售员', 3, 1, NULL, NULL)",
            "INSERT INTO agent_app.user_region_scope VALUES ('permission-user', 2, NULL), ('permission-user', 1, NULL)",
        ]
        for statement in statements:
            await connection.execute(text(statement))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_loader_binds_rep_to_active_sales_identity(permission_sessions):
    async with (
        permission_sessions() as app_session,
        permission_sessions() as sales_session,
    ):
        result = await load_data_scope(
            app_session, sales_session, user(role="SALES_REP", rep_id=11)
        )
    result.require_data()
    assert result.user_id == "permission-user"
    assert result.allowed_rep_ids == (11,)
    assert result.allowed_region_ids is None
    assert set(result.allowed_metrics) == {"sales_amount", "order_count"}


@pytest.mark.asyncio
async def test_manager_multiple_explicit_grants_and_director_global_scope(
    permission_sessions,
):
    async with (
        permission_sessions() as app_session,
        permission_sessions() as sales_session,
    ):
        manager = await load_data_scope(
            app_session, sales_session, user(role="SALES_MANAGER")
        )
        director = await load_data_scope(app_session, sales_session, user())
    assert manager.allowed_region_ids == (1, 2)
    assert manager.allowed_rep_ids is None
    assert director.allowed_region_ids is None
    assert director.allowed_rep_ids is None
    assert manager.scope_hash != director.scope_hash
    assert manager.scope_label and director.scope_label


@pytest.mark.asyncio
async def test_manager_preserves_inactive_region_history_and_ignores_missing_region(
    permission_sessions,
):
    async with (
        permission_sessions() as app_session,
        permission_sessions() as sales_session,
    ):
        await app_session.execute(
            text(
                "INSERT INTO agent_app.user_region_scope VALUES "
                "('history-user', 3, NULL), ('history-user', 999, NULL), ('missing-user', 999, NULL)"
            )
        )
        history = await load_data_scope(
            app_session,
            sales_session,
            user(id="history-user", role="SALES_MANAGER"),
        )
        missing = await load_data_scope(
            app_session,
            sales_session,
            user(id="missing-user", role="SALES_MANAGER"),
        )
    assert history.allowed_region_ids == (3,)
    history.require_data()
    assert missing.allowed_region_ids == ()
    with pytest.raises(HTTPException) as denied:
        missing.require_data()
    assert denied.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"role": "ADMIN"},
        {"role": "SALES_REP", "rep_id": None},
        {"role": "SALES_REP", "rep_id": 999},
        {"role": "SALES_REP", "rep_id": 12},
        {"active": 0},
        {"allowed_metrics_json": ["sales_amount", "arbitrary_sql"]},
        {"allowed_metrics_json": "sales_amount"},
        {"allowed_metrics_json": ["sales_amount", 1]},
    ],
)
async def test_invalid_user_permissions_fail_closed(permission_sessions, changes):
    async with (
        permission_sessions() as app_session,
        permission_sessions() as sales_session,
    ):
        with pytest.raises(HTTPException) as denied:
            await load_data_scope(app_session, sales_session, user(**changes))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_grants_or_metrics_deny_data_but_preserve_scope_for_history(
    permission_sessions,
):
    async with (
        permission_sessions() as app_session,
        permission_sessions() as sales_session,
    ):
        no_regions = await load_data_scope(
            app_session,
            sales_session,
            user(id="no-grants", role="SALES_MANAGER"),
        )
        no_metrics = await load_data_scope(
            app_session, sales_session, user(allowed_metrics_json=[])
        )
    assert no_regions.allowed_region_ids == ()
    assert no_metrics.allowed_metrics == ()
    for result in (no_regions, no_metrics):
        with pytest.raises(HTTPException) as denied:
            result.require_data()
        assert denied.value.status_code == 403


def test_scope_fingerprint_stable_and_changes_for_every_authorization_boundary():
    original = scope(
        allowed_region_ids=(1, 2), allowed_metrics=("sales_amount", "order_count")
    )
    equivalent = scope(
        allowed_region_ids=(2, 1), allowed_metrics=("order_count", "sales_amount")
    )
    assert original.scope_hash == equivalent.scope_hash
    assert len(original.scope_hash) == 64
    changes = [
        {"user_id": "another-user"},
        {"allowed_region_ids": (1,)},
        {"allowed_region_ids": None},
        {"allowed_rep_ids": (11,)},
        {"allowed_metrics": ("sales_amount",)},
        {"permission_version": 2},
    ]
    assert all(
        scope(allowed_region_ids=(1, 2), **change).scope_hash != original.scope_hash
        for change in changes
        if "allowed_region_ids" not in change
    )
    assert all(
        scope(**change).scope_hash != original.scope_hash
        for change in changes
        if "allowed_region_ids" in change
    )


def test_analysis_enforces_metric_average_dependencies_and_growth_permission():
    restricted = scope(allowed_metrics=("sales_amount",))
    restricted.require_analysis(spec())
    with pytest.raises(HTTPException):
        restricted.require_metric("gross_profit")
    with pytest.raises(HTTPException):
        restricted.require_analysis(spec(metric="profit"))
    with pytest.raises(HTTPException):
        restricted.require_analysis(spec(metric="average"))
    with pytest.raises(HTTPException):
        scope(allowed_metrics=("avg_order_value",)).require_analysis(
            spec(metric="average")
        )
    scope(
        allowed_metrics=("sales_amount", "order_count", "avg_order_value")
    ).require_analysis(
        spec(metric="average"),
    )
    with pytest.raises(HTTPException):
        restricted.require_analysis(spec(comparison="yoy"))
    scope(allowed_metrics=("sales_amount", "growth_rate")).require_analysis(
        spec(comparison="yoy")
    )


def test_analysis_explicit_region_cannot_expand_current_grants():
    restricted = scope(allowed_region_ids=(1, 2))
    restricted.require_analysis(spec())
    restricted.require_analysis(spec(scope=AnalysisScope(months=["2026-08"], region=2)))
    with pytest.raises(HTTPException) as denied:
        restricted.require_analysis(
            spec(scope=AnalysisScope(months=["2026-08"], region=3))
        )
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"comparison": "mom"},
        {"goal": "anomalies", "view": "trend"},
    ],
)
def test_mom_and_anomaly_deviation_require_growth_rate_permission(changes):
    with pytest.raises(HTTPException) as denied:
        scope(allowed_metrics=("sales_amount",)).require_analysis(spec(**changes))
    assert denied.value.status_code == 403
    scope(allowed_metrics=("sales_amount", "growth_rate")).require_analysis(
        spec(**changes)
    )
