"""真实事务、任务生命周期、授权与 SSE 回放；不连接开发数据库。"""

import asyncio
import json
import time
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from fastapi import Header
from pydantic import SecretStr
from sqlalchemy import CheckConstraint, MetaData, String, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from salesmind.api.auth import get_current_user, require_claims
from salesmind.core.config import Settings
from salesmind.core.security import digest_token
from salesmind.db.entities import (
    AgentAppBase,
    AnalysisRun,
    AppUser,
    ChatSession,
    DatasetVersion,
    RunEvent,
    SalesDataBase,
    SalesRegion,
    SalesRep,
    UserRegionScope,
)
from salesmind.db.session import Database
from salesmind.main import create_app
from salesmind.services.auth import session_key
from salesmind.services.conversation_schemas import AnalysisSpec, RunCreate, RunResume
from salesmind.services.conversations import TERMINAL, Conversations, now
from salesmind.services.intent import IntentCatalog, IntentDraft
from salesmind.services.permissions import METRICS
from salesmind.services.query_cache import SalesQueryCache
from salesmind.services.sales_overview import AccessInfo, OverviewResponse, RegionItem
from salesmind.services.task_supervisor import TaskSupervisor

OWNER = str(uuid4())
OTHER = str(uuid4())
LOGIN_ID = str(uuid4())
SPEC = {
    "scope": {"months": ["2026-08"], "region": 1},
    "metric": "amount",
    "view": "trend",
    "chart": "line",
    "comparison": "none",
    "sort_by": "value",
    "sort_order": "desc",
    "top_n": None,
    "goal": "overview",
}
SPEC = AnalysisSpec.model_validate(SPEC).model_dump(mode="json")
BODY = {"question": "分析2026年8月华东销售额", "analysis": SPEC}
RESULT = OverviewResponse.model_validate(
    {
        "source": "mysql",
        "queried_at": "2026-10-07T01:00:00Z",
        "months": ["2026-08"],
        "region_id": 1,
        "baseline_months": ["2025-08"],
        "baseline_covered": True,
        "comparison": "none",
        "totals": {
            "sales_amount": "120.00",
            "gross_profit": "50.00",
            "order_count": 1,
            "quantity": 1,
        },
        "dataset": {
            "version": 1,
            "available_from": "2024-02-01",
            "complete_through": "2026-09-21",
            "order_count": 1,
            "regions": [{"id": 1, "name": "华东"}],
            "products": [{"id": 1, "name": "商品", "category": "数码"}],
            "cells": [
                {
                    "month": "2026-08",
                    "region": 1,
                    "product": 1,
                    "amount": "12000",
                    "cost": "7000",
                    "count": 1,
                    "quantity": 1,
                }
            ],
        },
    }
)


@pytest_asyncio.fixture
async def environment(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        sales_database_url=None,
        app_database_url=None,
        redis_url=None,
        llm_api_key=None,
        sse_poll_seconds=0.05,
        run_queue_limit=2,
        run_concurrency=1,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'main.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def attach(connection, record):
        connection.execute(
            "ATTACH DATABASE ? AS agent_app", (str(tmp_path / "app.sqlite"),)
        )
        connection.execute(
            "ATTACH DATABASE ? AS sales_data", (str(tmp_path / "sales.sqlite"),)
        )
        connection.execute("PRAGMA foreign_keys=ON")

    # 只将 MySQL 的类型与时间默认值替换成测试 SQLite 能表达的形式；保留唯一约束与外键。
    metadata = MetaData()
    for original in [
        *AgentAppBase.metadata.tables.values(),
        *SalesDataBase.metadata.tables.values(),
    ]:
        table = original.to_metadata(metadata)
        for column in table.columns:
            column.type = column.type.as_generic()
            if isinstance(column.type, String):
                column.type.collation = None
            column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, CheckConstraint) and "JSON_TYPE" in str(
                constraint.sqltext
            ):
                table.constraints.remove(constraint)
    async with engine.begin() as db:
        await db.run_sync(metadata.create_all)
    database = Database(settings)
    database.app_engine = database.sales_engine = engine
    database._app_sessions = database._sales_sessions = async_sessionmaker(
        engine, expire_on_commit=False
    )
    users = {}
    async with database.app_session() as db, db.begin():
        db.add_all(
            [
                SalesRegion(
                    id=rid, name=name, active=1, created_at=now(), updated_at=now()
                )
                for rid, name in ((1, "华东"), (2, "华南"), (3, "华北"))
            ]
        )
        db.add(
            DatasetVersion(
                id=1,
                version=1,
                refreshed_at=now(),
                available_from=date(2024, 2, 1),
                complete_through=date(2026, 9, 21),
            )
        )
        for uid in (OWNER, OTHER):
            user = AppUser(
                id=uid,
                username=uid,
                display_name="测试",
                password_hash="test-hash",
                role="SALES_DIRECTOR",
                allowed_metrics_json=sorted(METRICS),
                permission_version=1,
                active=1,
                created_at=now(),
                updated_at=now(),
            )
            db.add(user)
            users[uid] = user
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set(
        session_key(LOGIN_ID),
        json.dumps(
            {
                "user_id": OWNER,
                "password_stamp": digest_token("test-hash"),
                "expires_at": int(time.time()) + 300,
            }
        ),
        ex=300,
    )
    supervisor = TaskSupervisor(database, settings)
    app = create_app(settings)
    app.state.settings, app.state.database = settings, database
    app.state.redis, app.state.supervisor = SimpleNamespace(client=redis), supervisor

    async def user_override(x_test_owner: str = Header(default=OWNER)):
        async with database.app_session() as db:
            return await db.get(AppUser, x_test_owner)

    app.dependency_overrides[get_current_user] = user_override
    app.dependency_overrides[require_claims] = lambda: {
        "sub": OWNER,
        "sid": LOGIN_ID,
        "exp": int(time.time()) + 300,
    }

    async def query(*args):
        return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, supervisor, database, users, redis
    await supervisor.close()
    await redis.aclose()
    await engine.dispose()


async def session(client):
    response = await client.post("/api/v1/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def submit(client, sid, body=None, key=None):
    return await client.post(
        f"/api/v1/sessions/{sid}/runs",
        json=body or BODY,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def wait_run(supervisor, rid):
    task = supervisor.tasks.get(rid)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)


@pytest.mark.asyncio
async def test_supervisor_reuses_query_cache_but_recomputes_chart(environment, monkeypatch):
    client, supervisor, _, _, redis = environment
    supervisor.query_cache = SalesQueryCache(redis)
    calls = 0

    async def load(db, query, scope):
        nonlocal calls
        calls += 1
        return RESULT.model_copy(deep=True, update={
            "access": AccessInfo.model_validate(scope.public()), "comparison": query.comparison,
        })

    monkeypatch.setattr("salesmind.services.task_supervisor.query_sales_overview", load)
    sid = await session(client)
    artifacts = []
    for chart in ("line", "bar"):
        created = await submit(client, sid, {**BODY, "analysis": {**SPEC, "chart": chart}})
        assert created.status_code == 202
        rid = created.json()["id"]
        await wait_run(supervisor, rid)
        run = (await client.get(f"/api/v1/runs/{rid}")).json()
        assert run["status"] == "completed", run
        payload = (await client.get(f"/api/v1/artifacts/{run['artifact_id']}")).json()["payload"]
        artifacts.append(payload)
    assert calls == 1
    assert not artifacts[0]["cache"]["hit"] and artifacts[1]["cache"]["hit"]
    assert artifacts[0]["queried_at"] == artifacts[1]["queried_at"]
    assert artifacts[0]["analysis_result"]["figure"]["data"][0]["type"] == "scatter"
    assert artifacts[1]["analysis_result"]["figure"]["data"][0]["type"] == "bar"
    assert supervisor.query_cache._locks == {}


@pytest.mark.asyncio
async def test_persistence_result_and_full_sse_replay(environment):
    client, supervisor, database, users, _ = environment
    sid = await session(client)
    created = await submit(client, sid)
    assert created.status_code == 202, created.text
    rid = created.json()["id"]
    # 不订阅 SSE，任务仍然完成。
    await wait_run(supervisor, rid)
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["status"] == "completed" and run["artifact_id"]
    artifact = await client.get(f"/api/v1/artifacts/{run['artifact_id']}")
    assert artifact.json()["payload"]["totals"]["sales_amount"] == "120.00"
    response = await client.get(f"/api/v1/runs/{rid}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["seq"] for e in events] == list(range(1, run["last_event_seq"] + 1))
    assert events[-1]["type"] == "run.completed"
    replay = await client.get(
        f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": "3"}
    )
    assert "id: 3\n" not in replay.text and "id: 4\n" in replay.text
    history = (await client.get(f"/api/v1/sessions/{sid}/messages")).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    # 新服务对象直接读持久化历史，不依赖原执行器内存。
    restored = Conversations(database)
    assert (await restored.history(OWNER, sid, 50, 0))["runs"][0][
        "status"
    ] == "completed"
    assert (await restored.artifact(users[OWNER], run["artifact_id"]))["payload"][
        "totals"
    ]["sales_amount"] == "120.00"


@pytest.mark.asyncio
async def test_ownership_and_idempotency(environment):
    client, supervisor, _, _, _ = environment
    sid = await session(client)
    key = str(uuid4())
    first, second = await asyncio.gather(
        submit(client, sid, key=key), submit(client, sid, key=key)
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    rid = first.json()["id"]
    await wait_run(supervisor, rid)
    different = deepcopy(BODY)
    different["question"] = "另一问题"
    assert (await submit(client, sid, different, key)).status_code == 409
    foreign = {"X-Test-Owner": OTHER}
    for path in (f"/sessions/{sid}/messages", f"/runs/{rid}", f"/runs/{rid}/events"):
        assert (await client.get("/api/v1" + path, headers=foreign)).status_code == 404
    for action in ("cancel", "pause", "resume"):
        assert (
            await client.post(f"/api/v1/runs/{rid}/{action}", json={}, headers=foreign)
        ).status_code == 404
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert (
        await client.get(f"/api/v1/artifacts/{run['artifact_id']}", headers=foreign)
    ).status_code == 404
    assert (await client.get("/api/v1/sessions", headers=foreign)).json()["items"] == []


@pytest.mark.asyncio
async def test_cancellation_blocks_late_result_and_releases_session(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    entered, release = asyncio.Event(), asyncio.Event()

    async def late_query(*args):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # 模拟外部调用吞掉取消后仍返回；终态保护仍必须有效。
            return RESULT
        return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", late_query
    )
    sid = await session(client)
    rid = (await submit(client, sid)).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    assert (
        await client.post(
            f"/api/v1/runs/{rid}/resume", json={}, headers={"X-Test-Owner": OTHER}
        )
    ).status_code == 404
    assert (await submit(client, sid)).status_code == 409
    assert (await client.delete(f"/api/v1/sessions/{sid}")).status_code == 409
    cancellations = await asyncio.gather(
        client.post(f"/api/v1/runs/{rid}/cancel"),
        client.post(f"/api/v1/runs/{rid}/cancel"),
    )
    assert all(
        response.json().get("status") == "cancelled" for response in cancellations
    ), [(response.status_code, response.json()) for response in cancellations]
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["status"] == "cancelled" and run["artifact_id"] is None
    assert (await client.post(f"/api/v1/runs/{rid}/cancel")).json()[
        "status"
    ] == "cancelled"
    release.set()
    other = (await submit(client, sid)).json()["id"]
    await wait_run(supervisor, other)
    assert (await client.delete(f"/api/v1/sessions/{sid}")).status_code == 204
    assert (await client.get(f"/api/v1/runs/{rid}")).status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_and_restart_recovery(environment, monkeypatch):
    client, supervisor, database, users, _ = environment
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("salesmind.services.task_supervisor.query_sales_overview", slow)
    sid = await session(client)
    rid = (await submit(client, sid)).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    assert (await client.post(f"/api/v1/runs/{rid}/pause")).json()[
        "status"
    ] == "interrupted"
    assert (await submit(client, sid)).status_code == 409
    assert (
        await client.post(f"/api/v1/runs/{rid}/resume", json={"analysis": SPEC})
    ).status_code == 409

    async def success(*args):
        return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", success
    )
    resumed = await client.post(f"/api/v1/runs/{rid}/resume", json={})
    assert resumed.status_code == 202
    await wait_run(supervisor, rid)
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["attempt"] == 2
    # 模拟旧进程留在 queued；新 store 对象恢复，不需要旧协程。
    run, _ = await supervisor.store.create_run(
        users[OWNER], sid, RunCreate.model_validate(BODY), str(uuid4()), lambda: True
    )
    restored = Conversations(database)
    await restored.recover()
    assert (await restored.get_run(OWNER, run["id"]))["status"] == "interrupted"
    async with database.app_session() as db:
        chat = await db.get(ChatSession, sid)
        assert chat.active_run_id == run["id"]


@pytest.mark.asyncio
async def test_clarification_and_input_validation(environment):
    client, supervisor, _, _, _ = environment
    sid = await session(client)
    run = (
        await submit(client, sid, {"question": "销售趋势", "mode": "clarify"})
    ).json()
    assert run["status"] == "awaiting_input" and run["id"] not in supervisor.tasks
    assert (await client.post(f"/api/v1/runs/{run['id']}/pause")).status_code == 409
    assert (
        await client.post(f"/api/v1/runs/{run['id']}/resume", json={})
    ).status_code == 422
    assert (
        await client.post(
            f"/api/v1/runs/{run['id']}/resume",
            json={"analysis": SPEC, "reply": "2026年8月"},
        )
    ).status_code == 202
    await wait_run(supervisor, run["id"])
    history = (await client.get(f"/api/v1/sessions/{sid}/messages")).json()
    assert [m["kind"] for m in history["messages"]] == [
        "question",
        "clarification",
        "clarification_reply",
        "answer",
    ]
    invalid = deepcopy(BODY)
    invalid["analysis"]["scope"]["months"] = ["2026-06", "2026-08"]
    assert (await submit(client, sid, invalid)).status_code == 422
    invalid = deepcopy(BODY)
    invalid["analysis"]["sql"] = "SELECT *"
    assert (await submit(client, sid, invalid)).status_code == 422
    assert (
        await client.post(f"/api/v1/sessions/{sid}/runs", json=BODY)
    ).status_code == 422


@pytest.mark.asyncio
async def test_bounded_queue_and_query_timeout(environment, monkeypatch):
    client, supervisor, _, _, _ = environment
    supervisor.settings = supervisor.settings.model_copy(
        update={"sales_query_timeout": 0.1}
    )

    async def slow(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr("salesmind.services.task_supervisor.query_sales_overview", slow)
    sids = [await session(client) for _ in range(3)]
    first, second = await submit(client, sids[0]), await submit(client, sids[1])
    assert first.status_code == second.status_code == 202
    assert (await submit(client, sids[2])).status_code == 429
    for response in (first, second):
        await wait_run(supervisor, response.json()["id"])
        run = (await client.get(f"/api/v1/runs/{response.json()['id']}")).json()
        assert run["status"] == "failed" and run["error_code"] == "timeout"


@pytest.mark.asyncio
async def test_cursor_errors_expiry_and_changed_permissions(environment):
    client, supervisor, database, _, _ = environment
    sid = await session(client)
    rid = (await submit(client, sid)).json()["id"]
    await wait_run(supervisor, rid)
    for cursor in ("-1", "nonsense", "999999", "1.5"):
        assert (
            await client.get(
                f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": cursor}
            )
        ).status_code == 400
    async with database.app_session() as db, db.begin():
        run = await db.get(AnalysisRun, rid)
        run.replay_floor_seq = 2
    assert (await client.get(f"/api/v1/runs/{rid}/events")).status_code == 410
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.permission_version = 2
    assert (
        await client.get(f"/api/v1/artifacts/{run['artifact_id']}")
    ).status_code == 409


@pytest.mark.asyncio
async def test_shutdown_marks_active_tasks_interrupted(environment, monkeypatch):
    client, supervisor, database, _, _ = environment
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("salesmind.services.task_supervisor.query_sales_overview", slow)
    rid = (await submit(client, await session(client))).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    await supervisor.close()
    run = await supervisor.store.get_run(OWNER, rid)
    assert run["status"] == "interrupted"
    async with database.app_session() as db:
        event_types = list(
            await db.scalars(select(RunEvent.event_type).where(RunEvent.run_id == rid))
        )
        assert "run.interrupted" in event_types and not TERMINAL.intersection(
            {run["status"]}
        )


def test_spec_rejects_invalid_dates_and_unsafe_client_fields():
    for months in (["2026-13"], ["2026-08", "2026-08"], ["2026-08", "2026-07"]):
        invalid = deepcopy(SPEC)
        invalid["scope"]["months"] = months
        with pytest.raises(ValueError):
            AnalysisSpec.model_validate(invalid)
    with pytest.raises(ValueError):
        RunResume.model_validate({"owner_id": OTHER})


async def model_environment(environment, monkeypatch, outputs):
    _, supervisor, _, _, _ = environment
    supervisor.intent_parser.settings = supervisor.settings.model_copy(
        update={"llm_api_key": SecretStr("fake-test-key")}
    )
    catalog = IntentCatalog(
        date(2024, 2, 1), date(2026, 9, 21), (RegionItem(id=1, name="华东"),)
    )
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.load_intent_catalog",
        AsyncMock(return_value=catalog),
    )
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        content = outputs[min(len(requests) - 1, len(outputs) - 1)]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ]
            },
        )

    await supervisor.intent_parser.client.aclose()
    supervisor.intent_parser.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    )
    return requests, query


PARSED = {
    "metric": "amount",
    "metric_text": "销售额",
    "metric_status": "supported",
    "view": "trend",
    "region_name": "华东",
    "period": {"kind": "range", "start_month": "2026-08", "end_month": "2026-08"},
}


@pytest.mark.asyncio
async def test_raw_question_model_validation_query_and_replay(environment, monkeypatch):
    client, supervisor, database, _, _ = environment
    requests, query = await model_environment(environment, monkeypatch, [PARSED])
    sid = await session(client)
    body, key = {"question": "2026年8月华东销售额走势"}, str(uuid4())
    run = (await submit(client, sid, body, key)).json()
    await wait_run(supervisor, run["id"])
    updated = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert updated["status"] == "completed"
    assert updated["request"]["analysis"] == SPEC
    assert len(requests) == query.await_count == 1
    assert query.await_args.args[1] == AnalysisSpec.model_validate(SPEC).overview_query()
    # 完成后重试原始请求，仍然匹配原输入指纹，不重新调用模型。
    assert (await submit(client, sid, body, key)).json()["id"] == run["id"]
    async with database.app_session() as db:
        row = await db.get(AnalysisRun, run["id"])
        assert row.model_calls == 1
    replay = await client.get(f"/api/v1/runs/{run['id']}/events")
    assert "intent.resolved" in replay.text


@pytest.mark.asyncio
async def test_model_clarification_reply_keeps_same_run_and_reference_date(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    requests, query = await model_environment(
        environment, monkeypatch, [{**PARSED, "period": None}, PARSED]
    )
    sid = await session(client)
    run = (await submit(client, sid, {"question": "华东销售额走势"})).json()
    await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert current["status"] == "awaiting_input" and current[
        "clarification_fields"
    ] == ["months"]
    assert query.await_count == 0
    # 客户端不能绕过模型澄清，塞入自行构造的查询参数。
    assert (
        await client.post(f"/api/v1/runs/{run['id']}/resume", json={"analysis": SPEC})
    ).status_code == 422
    resumed = await client.post(
        f"/api/v1/runs/{run['id']}/resume", json={"reply": "2026年8月"}
    )
    assert resumed.status_code == 202 and resumed.json()["id"] == run["id"]
    await wait_run(supervisor, run["id"])
    assert (await client.get(f"/api/v1/runs/{run['id']}")).json()[
        "status"
    ] == "completed"
    inputs = [json.loads(request["messages"][1]["content"]) for request in requests]
    assert inputs[1]["question"] == inputs[0]["question"] == "华东销售额走势"
    assert inputs[1]["clarification_replies"] == ["2026年8月"]
    assert inputs[0]["reference_date"] == inputs[1]["reference_date"]
    assert query.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,status,code",
    [
        (
            {**PARSED, "region_name": None, "region_unknown": True},
            "awaiting_input",
            None,
        ),
        ({**PARSED, "region_name": "模型编造的区域"}, "failed", "invalid_model_output"),
        ({**PARSED, "unsupported": True}, "failed", "unsupported"),
        ({**PARSED, "sql": "SELECT *"}, "failed", "invalid_model_output"),
        ({**PARSED, "metric": "net_profit"}, "failed", "invalid_model_output"),
    ],
)
async def test_bad_model_conditions_never_query(
    environment, monkeypatch, output, status, code
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(environment, monkeypatch, [output])
    run = (
        await submit(
            client, await session(client), {"question": "2026年8月华东销售额走势"}
        )
    ).json()
    await wait_run(supervisor, run["id"])
    updated = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert updated["status"] == status and updated["error_code"] == code
    assert query.await_count == 0


@pytest.mark.asyncio
async def test_missing_model_key_fails_without_sales_query(environment, monkeypatch):
    client, supervisor, _, _, _ = environment
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    run = (
        await submit(client, await session(client), {"question": "2026年8月销售额"})
    ).json()
    await wait_run(supervisor, run["id"])
    updated = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert (
        updated["status"] == "failed"
        and updated["error_code"] == "model_not_configured"
    )
    assert query.await_count == 0


@pytest.mark.asyncio
async def test_cancellation_ignores_late_model_conditions(environment, monkeypatch):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(environment, monkeypatch, [PARSED])
    entered = asyncio.Event()

    async def late_parse(**kwargs):
        entered.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return IntentDraft.model_validate(PARSED)

    monkeypatch.setattr(supervisor.intent_parser, "parse", late_parse)
    run = (
        await submit(client, await session(client), {"question": "2026年8月销售额"})
    ).json()
    await asyncio.wait_for(entered.wait(), 2)
    assert (await client.post(f"/api/v1/runs/{run['id']}/cancel")).status_code == 200
    assert (await client.get(f"/api/v1/runs/{run['id']}")).json()[
        "status"
    ] == "cancelled"
    assert query.await_count == 0


@pytest.mark.asyncio
async def test_followup_context_is_owned_persisted_and_requeries(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    requests, query = await model_environment(
        environment, monkeypatch, [PARSED, {"follow_up": True, "region_name": "华南"}]
    )
    catalog = IntentCatalog(
        date(2024, 2, 1),
        date(2026, 9, 21),
        (RegionItem(id=1, name="华东"), RegionItem(id=2, name="华南")),
    )
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.load_intent_catalog",
        AsyncMock(return_value=catalog),
    )
    south = RESULT.model_copy(deep=True)
    south.region_id = 2
    south.dataset.regions.append(RegionItem(id=2, name="华南"))
    for c in south.dataset.cells:
        c.region = 2
    query.side_effect = [RESULT, south]
    sid = await session(client)
    first = (await submit(client, sid, {"question": "2026年8月华东销售额走势"})).json()
    await wait_run(supervisor, first["id"])
    second = (await submit(client, sid, {"question": "华南呢"})).json()
    assert second["context_run_id"] == first["id"]
    await wait_run(supervisor, second["id"])
    result = (await client.get(f"/api/v1/runs/{second['id']}")).json()
    assert result["status"] == "completed"
    spec = result["request"]["analysis"]
    assert (
        spec["scope"] == {"months": ["2026-08"], "region": 2}
        and spec["metric"] == "amount"
    )
    assert query.await_count == 2
    inputs = json.loads(requests[1]["messages"][1]["content"])
    assert inputs["previous_analysis"]["region_name"] == "华东"
    assert (
        "scope" not in inputs["previous_analysis"]
        and "dataset" not in inputs["previous_analysis"]
    )
    assert (await Conversations(database).get_run(OWNER, second["id"]))[
        "inherited_fields"
    ] == result["inherited_fields"]
    # 新会话没有跨会话历史，不能隐式继承。
    third = (await submit(client, await session(client), {"question": "华南呢"})).json()
    await wait_run(supervisor, third["id"])
    assert third["context_run_id"] is None
    assert (await client.get(f"/api/v1/runs/{third['id']}")).json()[
        "status"
    ] == "awaiting_input"
    assert query.await_count == 2


@pytest.mark.asyncio
async def test_complex_graph_runs_query_compute_rank_chart_and_persists_result(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    draft = {
        "metric": "amount",
        "metric_text": "销售额",
        "metric_status": "supported",
        "period": PARSED["period"],
        "region_all": True,
        "goal": "largest_decline",
    }
    _, query = await model_environment(environment, monkeypatch, [draft])
    result = RESULT.model_copy(deep=True)
    result.comparison = "yoy"
    result.region_id = None
    result.dataset.regions.append(RegionItem(id=2, name="华南"))
    row = result.dataset.cells[0]
    result.dataset.cells += [
        row.model_copy(update={"month": "2025-08", "amount": "15000"}),
        row.model_copy(update={"region": 2, "amount": "4000"}),
        row.model_copy(update={"month": "2025-08", "region": 2, "amount": "10000"}),
    ]
    result.totals.sales_amount = "160.00"
    result.totals.gross_profit = "20.00"
    result.totals.order_count = 2
    result.totals.quantity = 2
    query.return_value = result
    run = (
        await submit(
            client,
            await session(client),
            {"question": "比较2026年8月各区域销售额同比，找出下降最多的区域"},
        )
    ).json()
    await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert current["status"] == "completed", current
    nodes = [s["id"] for s in current["plan"]]
    assert nodes == [
        "understand",
        "plan",
        "query",
        "analyze",
        "rank",
        "visualize",
        "result",
    ]
    _, events = await supervisor.store.events(OWNER, run["id"], 0)
    assert [
        e["payload"]["stage"] for e in events if e["type"] == "stage.started"
    ] == nodes
    artifact = (await client.get(f"/api/v1/artifacts/{current['artifact_id']}")).json()[
        "payload"
    ]["analysis_result"]
    assert artifact["selected_ids"] == ["2"] and "60.00%" in artifact["headline"]
    assert artifact["figure"]["data"][0]["y"] == [-60, -20]
    assert query.await_args.args[1].region_id is None


@pytest.mark.asyncio
async def test_permission_change_prevents_context_reuse_and_query(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    _, query = await model_environment(
        environment, monkeypatch, [PARSED, {"follow_up": True, "region_name": "华东"}]
    )
    sid = await session(client)
    first = (await submit(client, sid, {"question": "2026年8月华东销售额走势"})).json()
    await wait_run(supervisor, first["id"])
    async with database.app_session() as db, db.begin():
        owner = await db.get(AppUser, OWNER)
        owner.permission_version += 1
    second = (await submit(client, sid, {"question": "华东呢"})).json()
    assert second["context_run_id"] is None
    await wait_run(supervisor, second["id"])
    assert (await client.get(f"/api/v1/runs/{second['id']}")).json()[
        "status"
    ] == "awaiting_input"
    assert query.await_count == 1


@pytest.mark.asyncio
async def test_multiple_clarification_rounds_do_not_query_early(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(
        environment,
        monkeypatch,
        [
            {"region_name": "华东"},
            {
                "region_name": "华东",
                "metric": "amount",
                "metric_text": "销售额",
                "metric_status": "supported",
            },
            PARSED,
        ],
    )
    run = (await submit(client, await session(client), {"question": "看看华东"})).json()
    await wait_run(supervisor, run["id"])
    for reply in ["销售额", "2026年8月"]:
        assert query.await_count == 0
        response = await client.post(
            f"/api/v1/runs/{run['id']}/resume", json={"reply": reply}
        )
        assert response.status_code == 202
        await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert current["status"] == "completed" and current["clarification_replies"] == [
        "销售额",
        "2026年8月",
    ]
    assert query.await_count == 1


async def checkpoint_artifacts(database, rid):
    from salesmind.db.entities import ResultArtifact

    async with database.app_session() as db:
        return list(
            await db.scalars(
                select(ResultArtifact).where(
                    ResultArtifact.run_id == rid,
                    ResultArtifact.artifact_key == "checkpoint:query",
                )
            )
        )


async def seed_running_query_checkpoint(environment):
    """留下已提交查询但仍 running 的数据库状态，模拟旧进程直接退出。"""
    client, supervisor, _, users, _ = environment
    sid = await session(client)
    run, _ = await supervisor.store.create_run(
        users[OWNER], sid, RunCreate.model_validate(BODY), str(uuid4()), lambda: True
    )
    rid = run["id"]
    attempt = (await supervisor.store.begin_attempt(OWNER, rid))["attempt"]
    steps = [
        {"id": node, "label": node}
        for node in ("understand", "plan", "query", "analyze", "visualize", "result")
    ]
    assert await supervisor.store.stage(OWNER, rid, "understand", completed=True)
    assert await supervisor.store.plan(OWNER, rid, steps)
    assert await supervisor.store.stage(OWNER, rid, "plan", completed=True)
    assert await supervisor.store.stage(OWNER, rid, "query")
    assert await supervisor.store.checkpoint_query(OWNER, rid, attempt, RESULT, 7)
    return rid, attempt


@pytest.mark.asyncio
async def test_query_checkpoint_survives_repeated_pause_resume(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    entered = asyncio.Queue()
    original_stage = supervisor.store.stage
    analyze_calls = 0

    async def block_analyze(owner, rid, stage, completed=False, **kwargs):
        nonlocal analyze_calls
        if stage == "analyze" and not completed:
            analyze_calls += 1
            if analyze_calls <= 2:
                entered.put_nowait(rid)
                await asyncio.Event().wait()
        return await original_stage(owner, rid, stage, completed=completed, **kwargs)

    monkeypatch.setattr(supervisor.store, "stage", block_analyze)
    rid = (await submit(client, await session(client))).json()["id"]
    for expected_attempt in (1, 2):
        assert await asyncio.wait_for(entered.get(), 5) == rid
        snapshots = await checkpoint_artifacts(database, rid)
        assert len(snapshots) == 1
        assert snapshots[0].schema_json["type"] == "sales.query-checkpoint.v1"
        assert snapshots[0].payload_json["totals"]["sales_amount"] == "120.00"
        assert query.await_count == 1
        paused = await client.post(f"/api/v1/runs/{rid}/pause")
        assert paused.json()["status"] == "interrupted"
        assert paused.json()["attempt"] == expected_attempt
        assert (
            await client.post(f"/api/v1/runs/{rid}/resume", json={})
        ).status_code == 202
    await wait_run(supervisor, rid)
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["status"] == "completed" and run["attempt"] == 3
    assert query.await_count == 1
    _, events = await supervisor.store.events(OWNER, rid, 0)
    completed = [
        event["payload"]["stage"]
        for event in events
        if event["type"] == "stage.completed"
    ]
    assert completed.count("understand") == completed.count("plan") == 1
    assert completed.count("query") == 1
    artifact = (await client.get(f"/api/v1/artifacts/{run['artifact_id']}")).json()
    assert artifact["payload"]["totals"]["sales_amount"] == "120.00"
    messages = (
        await client.get(f"/api/v1/sessions/{run['session_id']}/messages")
    ).json()
    assert sum(message["kind"] == "answer" for message in messages["messages"]) == 1


@pytest.mark.asyncio
async def test_new_supervisor_restores_query_checkpoint_after_hard_restart(
    environment, monkeypatch
):
    client, old_supervisor, database, _, _ = environment
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    rid, attempt = await seed_running_query_checkpoint(environment)
    restored = TaskSupervisor(database, old_supervisor.settings)
    client._transport.app.state.supervisor = restored
    try:
        await restored.ensure_started()
        current = await restored.store.get_run(OWNER, rid)
        assert current["status"] == "interrupted" and current["attempt"] == attempt
        resumed = await client.post(f"/api/v1/runs/{rid}/resume", json={})
        assert resumed.status_code == 202, resumed.text
        await wait_run(restored, rid)
        run = (await client.get(f"/api/v1/runs/{rid}")).json()
        assert run["status"] == "completed" and run["attempt"] == attempt + 1
        query.assert_not_awaited()
        _, events = await restored.store.events(OWNER, rid, 0)
        assert [
            event["payload"]["stage"]
            for event in events
            if event["type"] == "stage.started"
        ] == ["query", "analyze", "visualize", "result"]
        artifact = (await client.get(f"/api/v1/artifacts/{run['artifact_id']}")).json()
        assert artifact["payload"]["dataset"]["version"] == RESULT.dataset.version
        assert artifact["payload"]["totals"]["sales_amount"] == "120.00"
    finally:
        await restored.close()
        client._transport.app.state.supervisor = old_supervisor


@pytest.mark.asyncio
async def test_pause_during_query_has_no_checkpoint_and_requeries(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    entered = asyncio.Event()
    calls = 0

    async def query(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    rid = (await submit(client, await session(client))).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    assert (await client.post(f"/api/v1/runs/{rid}/pause")).json()[
        "status"
    ] == "interrupted"
    assert await checkpoint_artifacts(database, rid) == []
    assert (await client.post(f"/api/v1/runs/{rid}/resume", json={})).status_code == 202
    await wait_run(supervisor, rid)
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["status"] == "completed"
    assert calls == 2
    assert len(await checkpoint_artifacts(database, rid)) == 1


@pytest.mark.asyncio
async def test_cancelled_late_query_cannot_commit_checkpoint(environment, monkeypatch):
    client, supervisor, database, _, _ = environment
    entered = asyncio.Event()

    async def query_ignoring_cancel(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query_ignoring_cancel
    )
    rid = (await submit(client, await session(client))).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    response = await client.post(f"/api/v1/runs/{rid}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled", response.text
    await wait_run(supervisor, rid)
    assert await checkpoint_artifacts(database, rid) == []
    _, events = await supervisor.store.events(OWNER, rid, 0)
    assert not any(
        event["type"] == "stage.completed" and event["payload"]["stage"] == "query"
        for event in events
    )
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["artifact_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("damage", "status"),
    [
        ("expired", 410),
        ("permission", 409),
        ("schema", 409),
        ("payload", 409),
        ("valid_payload_changed", 409),
        ("missing", 409),
        ("input", 409),
        ("data_version", 409),
        ("graph", 409),
    ],
)
async def test_invalid_query_checkpoint_refuses_resume_without_requery(
    environment, monkeypatch, damage, status
):
    from datetime import timedelta

    from salesmind.db.entities import ResultArtifact

    client, supervisor, database, _, _ = environment
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    rid, _ = await seed_running_query_checkpoint(environment)
    await supervisor.store.finish_stop(OWNER, rid, "interrupted")
    async with database.app_session() as db, db.begin():
        snapshot = await db.scalar(
            select(ResultArtifact).where(
                ResultArtifact.run_id == rid,
                ResultArtifact.artifact_key == "checkpoint:query",
            )
        )
        if damage == "expired":
            snapshot.created_at = now() - timedelta(days=2)
            snapshot.expires_at = now() - timedelta(days=1)
        elif damage == "permission":
            user = await db.get(AppUser, OWNER)
            user.permission_version += 1
        elif damage == "schema":
            snapshot.schema_json = {**snapshot.schema_json, "type": "unknown.v1"}
        elif damage == "payload":
            snapshot.payload_json = {**snapshot.payload_json, "dataset": {}}
        elif damage == "valid_payload_changed":
            changed = deepcopy(snapshot.payload_json)
            changed["dataset"]["cells"][0]["amount"] = "99000"
            snapshot.payload_json = changed
        elif damage == "missing":
            await db.delete(snapshot)
        elif damage == "data_version":
            snapshot.data_version += 1
        else:
            run = await db.get(AnalysisRun, rid)
            if damage == "input":
                changed = deepcopy(run.request_json)
                changed["analysis"]["scope"]["months"] = ["2026-07"]
                run.request_json = changed
            elif damage == "graph":
                run.graph_version = "incompatible-v9"
    response = await client.post(f"/api/v1/runs/{rid}/resume", json={})
    assert response.status_code == status, response.text
    assert rid not in supervisor.tasks
    query.assert_not_awaited()
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["status"] == "interrupted"


@pytest.mark.asyncio
async def test_query_checkpoint_rejects_old_attempt_and_is_idempotent(environment):
    _, supervisor, database, users, _ = environment
    rid, first_attempt = await seed_running_query_checkpoint(environment)
    original = (await checkpoint_artifacts(database, rid))[0]
    _, before = await supervisor.store.events(OWNER, rid, 0)
    changed = RESULT.model_copy(deep=True)
    changed.totals.sales_amount = "999.00"
    assert await supervisor.store.checkpoint_query(
        OWNER, rid, first_attempt, changed, 7
    )
    duplicate = (await checkpoint_artifacts(database, rid))[0]
    _, after = await supervisor.store.events(OWNER, rid, 0)
    assert duplicate.id == original.id and after == before
    assert duplicate.payload_json["totals"]["sales_amount"] == "120.00"
    await supervisor.store.finish_stop(OWNER, rid, "interrupted")
    await supervisor.store.resume(users[OWNER], rid, RunResume(), lambda: True)
    current_attempt = (await supervisor.store.begin_attempt(OWNER, rid))["attempt"]
    assert current_attempt == first_attempt + 1
    assert not await supervisor.store.checkpoint_query(
        OWNER, rid, first_attempt, changed, 7
    )
    with pytest.raises(asyncio.CancelledError):
        await supervisor.store.restore_query(OWNER, rid, first_attempt)
    restored = await supervisor.store.restore_query(OWNER, rid, current_attempt)
    assert restored.model_dump(mode="json") == RESULT.model_dump(mode="json")
    snapshots = await checkpoint_artifacts(database, rid)
    assert len(snapshots) == 1 and snapshots[0].id == original.id


@pytest.mark.asyncio
async def test_old_attempt_cannot_stop_or_complete_resumed_query_checkpoint(
    environment,
):
    _, supervisor, database, users, _ = environment
    store = supervisor.store
    rid, old_attempt = await seed_running_query_checkpoint(environment)
    await store.finish_stop(OWNER, rid, "interrupted", attempt=old_attempt)
    queued = await store.resume(users[OWNER], rid, RunResume(), lambda: True)
    assert queued["status"] == "queued" and queued["attempt"] == old_attempt
    _, queued_events = await store.events(OWNER, rid, 0)
    # 新 attempt 在执行器取到任务时才递增，queued 期间也必须拒绝旧协程收尾。
    late_stop = await store.finish_stop(OWNER, rid, "cancelled", attempt=old_attempt)
    await store.fail(OWNER, rid, "late_failure", "旧执行异常", attempt=old_attempt)
    assert late_stop["status"] == "queued"
    assert (await store.get_run(OWNER, rid))["status"] == "queued"
    _, events = await store.events(OWNER, rid, 0)
    assert events == queued_events
    new_attempt = (await store.begin_attempt(OWNER, rid))["attempt"]
    assert new_attempt == old_attempt + 1
    _, running_events = await store.events(OWNER, rid, 0)
    assert not await store.stage(
        OWNER, rid, "analyze", completed=True, attempt=old_attempt
    )
    await store.complete(OWNER, rid, RESULT, 7, attempt=old_attempt)
    await store.fail(OWNER, rid, "late_failure", "旧执行异常", attempt=old_attempt)
    late_stop = await store.finish_stop(OWNER, rid, "cancelled", attempt=old_attempt)
    assert late_stop["status"] == "running"
    _, events = await store.events(OWNER, rid, 0)
    assert events == running_events
    run = await store.get_run(OWNER, rid)
    assert run["status"] == "running" and run["artifact_id"] is None
    restored = await store.restore_query(OWNER, rid, new_attempt)
    assert restored.model_dump(mode="json") == RESULT.model_dump(mode="json")
    async with database.app_session() as db:
        chat = await db.get(ChatSession, run["session_id"])
        assert chat.active_run_id == rid


@pytest.mark.asyncio
async def test_query_checkpoint_and_completed_event_rollback_together(
    environment, monkeypatch
):
    client, supervisor, database, users, _ = environment
    store = supervisor.store
    run, _ = await store.create_run(
        users[OWNER],
        await session(client),
        RunCreate.model_validate(BODY),
        str(uuid4()),
        lambda: True,
    )
    rid = run["id"]
    attempt = (await store.begin_attempt(OWNER, rid))["attempt"]
    await store.stage(OWNER, rid, "query")
    original_event = store.event

    def fail_query_completion(db, run, event_type, payload):
        if event_type == "stage.completed" and payload["stage"] == "query":
            raise RuntimeError("模拟节点完成事件写入失败")
        return original_event(db, run, event_type, payload)

    monkeypatch.setattr(store, "event", fail_query_completion)
    with pytest.raises(RuntimeError, match="模拟节点完成事件写入失败"):
        await store.checkpoint_query(OWNER, rid, attempt, RESULT, 7)
    assert await checkpoint_artifacts(database, rid) == []
    async with database.app_session() as db:
        persisted = await db.get(AnalysisRun, rid)
        assert "_query_checkpoint_id" not in persisted.request_json
        assert persisted.node_steps == persisted.tool_calls == 0
        assert persisted.data_version is None
    _, events = await store.events(OWNER, rid, 0)
    assert not any(event["type"] == "stage.completed" for event in events)
    assert await store.restore_query(OWNER, rid, attempt) is None


@pytest.mark.asyncio
async def test_cancel_query_checkpoint_with_unchanged_timestamp(
    environment, monkeypatch
):
    _, supervisor, database, _, _ = environment
    rid, attempt = await seed_running_query_checkpoint(environment)
    async with database.app_session() as db:
        run = await db.get(AnalysisRun, rid)
        unchanged_timestamp = run.updated_at
    monkeypatch.setattr(
        "salesmind.services.conversations.now", lambda: unchanged_timestamp
    )
    # 时钟分辨率导致两次事件时间相同时，flush 会让 server_onupdate 列过期。
    # 返回值序列化仍必须通过显式异步读取完成，不能同步触发数据库 IO。
    response = await supervisor.store.finish_stop(
        OWNER, rid, "cancelled", attempt=attempt
    )
    assert response["status"] == "cancelled"
    assert response["updated_at"].endswith("Z")


@pytest.mark.asyncio
@pytest.mark.parametrize("metric,comparison", [("profit", "none"), ("amount", "yoy")])
async def test_structured_analysis_rejects_ungranted_metrics(
    environment, monkeypatch, metric, comparison
):
    client, supervisor, database, _, _ = environment
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.allowed_metrics_json = ["sales_amount"]
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    body = deepcopy(BODY)
    body["analysis"].update(metric=metric, comparison=comparison)
    response = await submit(client, await session(client), body)
    assert response.status_code == 403
    assert supervisor.tasks == {}
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_cannot_grant_metric_permissions(environment, monkeypatch):
    client, supervisor, database, _, _ = environment
    _, query = await model_environment(
        environment,
        monkeypatch,
        [{**PARSED, "metric": "profit", "metric_text": "毛利"}],
    )
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.allowed_metrics_json = ["sales_amount"]
    response = await submit(
        client, await session(client), {"question": "2026年8月华东毛利"}
    )
    assert response.status_code == 202
    rid = response.json()["id"]
    await wait_run(supervisor, rid)
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["status"] == "failed" and run["error_code"] == "permission_denied"
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_ungranted_region_is_denied_before_model(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    supervisor.intent_parser.settings = supervisor.settings.model_copy(
        update={"llm_api_key": SecretStr("fake-test-key")}
    )
    parser = AsyncMock(
        return_value=IntentDraft.model_validate(
            {**PARSED, "region_name": None, "region_all": True}
        )
    )
    query = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(supervisor.intent_parser, "parse", parser)
    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.role = "SALES_MANAGER"
        db.add(UserRegionScope(user_id=OWNER, region_id=1, created_at=now()))
    sid = await session(client)
    body = deepcopy(BODY)
    body["analysis"]["scope"]["region"] = 2
    assert (await submit(client, sid, body)).status_code == 403
    response = await submit(client, sid, {"question": "2026年8月华南销售额"})
    assert response.status_code == 202
    rid = response.json()["id"]
    await wait_run(supervisor, rid)
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["status"] == "failed" and run["error_code"] == "permission_denied"
    parser.assert_not_awaited()
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_sales_rep_requires_current_active_binding(environment):
    client, supervisor, database, _, _ = environment
    async with database.app_session() as db, db.begin():
        db.add(
            SalesRep(
                id=7,
                name="销售员",
                region_id=1,
                active=0,
                created_at=now(),
                updated_at=now(),
            )
        )
        user = await db.get(AppUser, OWNER)
        user.role, user.rep_id = "SALES_REP", 7
    response = await submit(client, await session(client))
    assert response.status_code == 403
    assert supervisor.tasks == {}


@pytest.mark.asyncio
async def test_scope_change_without_version_hides_history_and_blocks_replay(
    environment,
):
    client, supervisor, database, stale_users, _ = environment
    sid, key = await session(client), str(uuid4())
    created = await submit(client, sid, key=key)
    rid = created.json()["id"]
    await wait_run(supervisor, rid)
    artifact_id = (await client.get(f"/api/v1/runs/{rid}")).json()["artifact_id"]
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.allowed_metrics_json = ["order_count"]
    run = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert run["error_code"] == "access_changed" and run["artifact_id"] is None
    assert run["plan"] == [] and run["metric_resolution"] is None
    assert run["request"]["question"] == BODY["question"]
    assert "analysis" not in run["request"]
    history = (await client.get(f"/api/v1/sessions/{sid}/messages")).json()
    assert [(m["role"], m["kind"]) for m in history["messages"]] == [
        ("user", "question"),
        ("assistant", "error"),
    ]
    assert "120.00" not in json.dumps(history, ensure_ascii=False)
    assert (await client.get(f"/api/v1/runs/{rid}/events")).status_code == 409
    assert (await client.get(f"/api/v1/artifacts/{artifact_id}")).status_code == 409
    # 即使调用方仍持有旧登录快照，也必须读取数据库中的新授权。
    with pytest.raises(Exception) as caught:
        await supervisor.store.artifact(stale_users[OWNER], artifact_id)
    assert caught.value.status_code == 409
    assert (await client.post(f"/api/v1/runs/{rid}/cancel")).json()[
        "artifact_id"
    ] is None


@pytest.mark.asyncio
async def test_permissions_rechecked_after_query_before_checkpoint(
    environment, monkeypatch
):
    client, supervisor, database, _, _ = environment
    entered, released = asyncio.Event(), asyncio.Event()

    async def query(*args):
        entered.set()
        await released.wait()
        return RESULT

    monkeypatch.setattr(
        "salesmind.services.task_supervisor.query_sales_overview", query
    )
    rid = (await submit(client, await session(client))).json()["id"]
    await asyncio.wait_for(entered.wait(), 5)
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.allowed_metrics_json = ["order_count"]
    released.set()
    await wait_run(supervisor, rid)
    async with database.app_session() as db:
        run = await db.get(AnalysisRun, rid)
        assert run.status == "failed" and run.error_code == "access_changed"
    assert await checkpoint_artifacts(database, rid) == []
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["artifact_id"] is None


@pytest.mark.asyncio
async def test_scope_change_prevents_checkpoint_resume_without_version_bump(
    environment,
):
    client, supervisor, database, _, _ = environment
    rid, attempt = await seed_running_query_checkpoint(environment)
    await supervisor.store.finish_stop(OWNER, rid, "interrupted", attempt=attempt)
    async with database.app_session() as db, db.begin():
        user = await db.get(AppUser, OWNER)
        user.allowed_metrics_json = ["order_count"]
    response = await client.post(f"/api/v1/runs/{rid}/resume", json={})
    assert response.status_code == 409
    assert rid not in supervisor.tasks
    cancelled = await client.post(f"/api/v1/runs/{rid}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert "analysis" not in cancelled.json()["request"]
