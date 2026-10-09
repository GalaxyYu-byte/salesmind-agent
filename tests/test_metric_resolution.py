"""不可信模型的指标映射、未知指标阻断与原词持久化。"""

import json

import pytest
from intent_cases import CATALOG, REFERENCE
from test_conversations import (
    OWNER,
    PARSED,
    model_environment,
    session,
    submit,
    wait_run,
)
from test_conversations import environment as conversation_environment

from salesmind.services.intent import IntentDraft, normalize_intent
from salesmind.services.metric_resolution import verify_metric

environment = conversation_environment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,output,status,text,metric_status",
    [
        (
            "2026年8月华东净利润多少？",
            {**PARSED, "metric": "profit", "metric_text": "净利润"},
            "failed",
            "净利润",
            "unknown",
        ),
        (
            "2026年8月华东净利润多少？",
            {**PARSED, "metric": "profit", "metric_text": "毛利"},
            "failed",
            "净利润",
            "unknown",
        ),
        (
            "2026年8月华东销售额多少？",
            {**PARSED, "metric_text": None, "metric_status": None},
            "awaiting_input",
            "销售额",
            "ambiguous",
        ),
        (
            "2026年8月华东利润多少？",
            {**PARSED, "metric": "profit", "metric_text": "利润"},
            "awaiting_input",
            "利润",
            "ambiguous",
        ),
        (
            "2026年8月华东销售额多少？",
            {**PARSED, "metric": "profit"},
            "awaiting_input",
            "销售额",
            "ambiguous",
        ),
        ("2026年8月华东订单数多少？", PARSED, "awaiting_input", "订单数", "ambiguous"),
        (
            "2026年8月华东复购率多少？",
            {**PARSED, "metric_text": "复购率"},
            "failed",
            "复购率",
            "unknown",
        ),
        (
            "2026年8月华东销售额和订单数多少？",
            PARSED,
            "failed",
            "销售额、订单数",
            "unknown",
        ),
    ],
)
async def test_bad_metric_mapping_never_reaches_query(
    environment, monkeypatch, question, output, status, text, metric_status
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(environment, monkeypatch, [output])
    created = await submit(client, await session(client), {"question": question})
    assert created.status_code == 202
    rid = created.json()["id"]
    await wait_run(supervisor, rid)
    current = (await client.get(f"/api/v1/runs/{rid}")).json()
    assert current["status"] == status
    assert query.await_count == 0
    assert current["request"].get("analysis") is None
    resolution = {"text": text, "status": metric_status, "metric": None}
    assert current["metric_resolution"] == resolution
    assert (await supervisor.store.get_run(OWNER, rid))[
        "metric_resolution"
    ] == resolution
    if status == "awaiting_input":
        assert current["clarification_fields"] == ["metric"]
    else:
        assert current["error_code"] == "unsupported"
        assert text in current["error_message"]
        assert "销售额、订单数、毛利、平均客单价" in current["error_message"]
    _, events = await supervisor.store.events(OWNER, rid, 0)
    assert not any(
        e["type"] == "stage.started" and e["payload"]["stage"] == "query"
        for e in events
    )
    decision_event = next(
        e for e in events if e["type"] in {"run.failed", "clarification.required"}
    )
    assert decision_event["payload"]["metric_resolution"] == resolution


@pytest.mark.asyncio
async def test_unknown_metric_followup_cannot_inherit_previous_sales_amount(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(
        environment, monkeypatch, [PARSED, {"follow_up": True}]
    )
    sid = await session(client)
    first = (await submit(client, sid, {"question": "2026年8月华东销售额走势"})).json()
    await wait_run(supervisor, first["id"])
    second = (await submit(client, sid, {"question": "改看转化率呢？"})).json()
    await wait_run(supervisor, second["id"])
    current = (await client.get(f"/api/v1/runs/{second['id']}")).json()
    assert current["status"] == "failed" and current["error_code"] == "unsupported"
    assert current["metric_resolution"] == {
        "text": "转化率",
        "status": "unknown",
        "metric": None,
    }
    assert query.await_count == 1


@pytest.mark.asyncio
async def test_clarification_correction_to_unknown_metric_overrides_original(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    # 模型仍返回原销售额，服务端必须发现最后补充已改为净利润。
    requests, query = await model_environment(
        environment, monkeypatch, [{**PARSED, "period": None}, PARSED]
    )
    run = (
        await submit(client, await session(client), {"question": "华东销售额走势"})
    ).json()
    await wait_run(supervisor, run["id"])
    response = await client.post(
        f"/api/v1/runs/{run['id']}/resume", json={"reply": "改看2026年8月净利润"}
    )
    assert response.status_code == 202
    await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert (
        current["status"] == "failed"
        and current["metric_resolution"]["text"] == "净利润"
    )
    assert query.await_count == 0
    assert json.loads(requests[-1]["messages"][1]["content"])[
        "clarification_replies"
    ] == ["改看2026年8月净利润"]


@pytest.mark.asyncio
async def test_negated_unknown_metric_does_not_block_corrected_gross_profit(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    output = {**PARSED, "metric": "profit", "metric_text": "毛利"}
    _, query = await model_environment(environment, monkeypatch, [output])
    run = (
        await submit(
            client,
            await session(client),
            {"question": "2026年8月华东不是净利润，改看毛利走势"},
        )
    ).json()
    await wait_run(supervisor, run["id"])
    current = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert (
        current["status"] == "completed"
        and current["request"]["analysis"]["metric"] == "profit"
    )
    assert current["metric_resolution"] == {
        "text": "毛利",
        "status": "supported",
        "metric": "profit",
    }
    assert query.await_count == 1


@pytest.mark.parametrize(
    "term,metric",
    [
        ("卖了多少钱", "amount"),
        ("订单数量", "count"),
        ("毛利润", "profit"),
        ("客单价", "average"),
    ],
)
def test_registered_metric_synonyms_have_explicit_mapping(term, metric):
    evidence = verify_metric(term, "supported", metric, [f"2026年8月华东{term}"])
    assert (evidence.text, evidence.status, evidence.metric) == (
        term,
        "supported",
        metric,
    )


@pytest.mark.parametrize(
    "question,status",
    [
        ("销售额和毛利，换成柱状图", "unknown"),
        ("销售额换成柱状图，毛利也展示", "unknown"),
        ("销售额改看毛利", "supported"),
        ("毛利，不要看净利润", "supported"),
        ("不再看净利润，毛利", "supported"),
        ("毛利，不需要统计净利润", "supported"),
    ],
)
def test_only_metric_corrections_override_other_active_metrics(question, status):
    evidence = verify_metric("毛利", "supported", "profit", [question])
    assert evidence.status == status
    assert evidence.metric == ("profit" if status == "supported" else None)


def test_unregistered_metric_preserves_full_original_phrase():
    evidence = verify_metric("净利润率", "supported", "profit", ["华东净利润率"])
    assert (evidence.text, evidence.status, evidence.metric) == (
        "净利润率",
        "unknown",
        None,
    )


@pytest.mark.asyncio
async def test_omitted_explicit_metric_followup_does_not_inherit_wrong_metric(
    environment, monkeypatch
):
    client, supervisor, _, _, _ = environment
    _, query = await model_environment(
        environment, monkeypatch, [PARSED, {"follow_up": True}]
    )
    sid = await session(client)
    first = (await submit(client, sid, {"question": "2026年8月华东销售额走势"})).json()
    await wait_run(supervisor, first["id"])
    second = (await submit(client, sid, {"question": "改看毛利呢？"})).json()
    await wait_run(supervisor, second["id"])
    current = (await client.get(f"/api/v1/runs/{second['id']}")).json()
    assert current["status"] == "awaiting_input" and current[
        "clarification_fields"
    ] == ["metric"]
    assert current["metric_resolution"] == {
        "text": "毛利",
        "status": "ambiguous",
        "metric": None,
    }
    assert query.await_count == 1


@pytest.mark.parametrize(
    "inputs,text,metric,expected_text,expected_status",
    [
        (["2026年8月华东毛利润率是多少"], "毛利润", "profit", "毛利润率", "unknown"),
        (
            ["2026年8月华东利润是多少", "不要毛利"],
            "毛利",
            "profit",
            "毛利",
            "ambiguous",
        ),
        (
            ["净利润和毛利，毛利改成销售额"],
            "销售额",
            "amount",
            "净利润、销售额",
            "unknown",
        ),
        (["2026年8月华东毛利", "不要毛利"], "毛利", "profit", "毛利", "ambiguous"),
    ],
)
def test_unknown_suffix_negation_and_partial_correction_cannot_query(
    inputs, text, metric, expected_text, expected_status
):
    evidence = verify_metric(text, "supported", metric, inputs)
    assert (evidence.text, evidence.status, evidence.metric) == (
        expected_text,
        expected_status,
        None,
    )
    draft = IntentDraft.model_validate(
        {
            **PARSED,
            "metric_text": evidence.text,
            "metric_status": evidence.status,
            "metric": evidence.metric,
        }
    )
    decision = normalize_intent(draft, REFERENCE, CATALOG)
    assert decision.kind in {"clarify", "unsupported"} and decision.analysis is None
