"""补充回答与历史追问的边界；验证未补齐前不会查询业务数据。"""

import json

import pytest
from test_conversations import (
    PARSED,
    model_environment,
    session,
    submit,
    wait_run,
)
from test_conversations import environment as conversation_environment

environment = conversation_environment


@pytest.mark.asyncio
async def test_initial_followup_without_history_still_asks_for_context(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(
        environment, monkeypatch, [{"follow_up": True, "region_name": "华东"}]
    )
    run = (await submit(client, await session(client), {"question": "华东呢"})).json()
    await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert current["status"] == "awaiting_input"
    assert current["clarification_fields"] == ["context"]
    assert query.await_count == 0


@pytest.mark.asyncio
async def test_metric_correction_without_history_only_asks_for_missing_time(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    profit = {
        **PARSED,
        "metric": "profit",
        "metric_text": "毛利",
        "metric_status": "supported",
        "follow_up": True,
    }
    requests, query = await model_environment(
        environment,
        monkeypatch,
        [{**PARSED, "period": None}, {**profit, "period": None}, profit],
    )
    sid = await session(client)
    run = (await submit(client, sid, {"question": "最近华东销售额走势如何？"})).json()
    rid = run["id"]
    await wait_run(supervisor, rid)
    for reply in ["改看毛利", "2026年8月"]:
        current = (await client.get(f"/api/v1/runs/{rid}")).json()
        assert current["status"] == "awaiting_input"
        assert current["clarification_fields"] == ["months"]
        assert current["context_run_id"] is None
        assert query.await_count == 0
        resumed = await client.post(f"/api/v1/runs/{rid}/resume", json={"reply": reply})
        assert resumed.status_code == 202 and resumed.json()["id"] == rid
        await wait_run(supervisor, rid)

    current = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert current["status"] == "completed"
    assert current["request"]["analysis"]["metric"] == "profit"
    assert current["request"]["analysis"]["scope"] == {
        "months": ["2026-08"],
        "region": 1,
    }
    assert current["inherited_fields"] == []
    assert query.await_count == 1
    inputs = [json.loads(request["messages"][1]["content"]) for request in requests]
    assert [item["clarification_replies"] for item in inputs] == [
        [],
        ["改看毛利"],
        ["改看毛利", "2026年8月"],
    ]
    assert all(item["question"] == "最近华东销售额走势如何？" for item in inputs)
    assert all(item["previous_analysis"] is None for item in inputs)


@pytest.mark.asyncio
async def test_clarification_with_previous_analysis_preserves_inheritance(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    requests, query = await model_environment(
        environment,
        monkeypatch,
        [
            PARSED,
            {"follow_up": True, "region_unknown": True},
            {"follow_up": True, "region_name": "华东"},
        ],
    )
    sid = await session(client)
    first = (await submit(client, sid, {"question": "2026年8月华东销售额走势"})).json()
    await wait_run(supervisor, first["id"])
    first = (await client.get(f"/api/v1/runs/{first['id']}")).json()
    second = (await submit(client, sid, {"question": "西部呢"})).json()
    rid = second["id"]
    await wait_run(supervisor, rid)
    current = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert current["status"] == "awaiting_input"
    assert current["clarification_fields"] == ["region"]
    assert current["context_run_id"] == first["id"]
    assert query.await_count == 1

    resumed = await client.post(
        f"/api/v1/runs/{rid}/resume", json={"reply": "还是华东"}
    )
    assert resumed.status_code == 202
    await wait_run(supervisor, rid)
    current = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert current["status"] == "completed"
    assert current["request"]["analysis"] == first["request"]["analysis"]
    assert current["inherited_fields"] == [
        "metric",
        "months",
        "view",
        "chart",
        "comparison",
    ]
    assert query.await_count == 2
    inputs = json.loads(requests[-1]["messages"][1]["content"])
    assert inputs["question"] == "西部呢"
    assert inputs["clarification_replies"] == ["还是华东"]
    assert inputs["previous_analysis"]["metric"] == "amount"
    assert inputs["previous_analysis"]["months"] == ["2026-08"]
