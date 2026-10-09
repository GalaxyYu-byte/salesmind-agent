"""意图条件归一化与 DeepSeek 协议；使用模拟模型，不发送真实请求。"""

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
import test_sales_overview
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from salesmind.core.config import Settings
from salesmind.services.intent import (
    SYSTEM_PROMPT,
    IntentCatalog,
    IntentDraft,
    IntentError,
    IntentParser,
    load_intent_catalog,
    normalize_intent,
)
from salesmind.services.permissions import DataScope
from salesmind.services.sales_overview import ProductItem, RegionItem

sales_sessions = test_sales_overview.sessions

CATALOG = IntentCatalog(
    date(2024, 2, 1),
    date(2026, 9, 21),
    (RegionItem(id=1, name="华东"), RegionItem(id=2, name="华南")),
)
REFERENCE = date(2026, 10, 8)
DRAFT = {
    "metric": "amount",
    "metric_text": "销售额",
    "metric_status": "supported",
    "period": {"kind": "range", "start_month": "2026-08", "end_month": "2026-08"},
    "region_name": "华东",
    "view": "trend",
}


def normalize(**changes):
    return normalize_intent(
        IntentDraft.model_validate({**DRAFT, **changes}), REFERENCE, CATALOG
    )


def test_four_slots_resolve_to_query_parameters():
    decision = normalize()
    assert decision.kind == "ready"
    assert decision.analysis.model_dump() == {
        "scope": {"months": ["2026-08"], "region": 1},
        "metric": "amount",
        "view": "trend",
        "chart": "line",
        "comparison": "none",
        "sort_by": "value",
        "sort_order": "desc",
        "top_n": None,
        "goal": "overview",
        "product_ids": [],
        "anomaly_window": 3,
        "anomaly_threshold_percent": 30,
    }
    assert decision.analysis.overview_query().region_id == 1


def test_missing_fields_and_unknown_region_require_clarification():
    result = normalize(metric=None, period=None, region_name="不存在的区域")
    assert result.kind == "clarify" and result.analysis is None
    assert set(result.fields) == {"metric", "months", "region"}
    assert "华东" in result.message


def test_default_all_regions_and_summary():
    result = normalize(region_name=None, view=None)
    assert result.analysis.scope.region == 0
    assert result.analysis.view == "summary" and result.analysis.chart == "bar"


def test_unknown_region_marker_requires_clarification_instead_of_all_regions():
    result = normalize(region_name=None, region_unknown=True)
    assert result.kind == "clarify" and result.fields == ("region",)
    assert result.analysis is None


def test_relative_dates_use_calendar_not_latest_available_data():
    result = normalize(period={"kind": "last_month"})
    assert result.kind == "clarify" and result.fields == ("months",)
    assert "2026-09-21" in result.message
    complete_catalog = IntentCatalog(
        date(2024, 2, 1), date(2026, 9, 30), CATALOG.regions
    )
    draft = IntentDraft.model_validate(
        {**DRAFT, "period": {"kind": "recent_months", "count": 3}}
    )
    result = normalize_intent(draft, REFERENCE, complete_catalog)
    assert result.analysis.scope.months == ["2026-07", "2026-08", "2026-09"]


def test_explicit_latest_complete_months_anchor_to_watermark():
    result = normalize(period={"kind": "latest_complete_months", "count": 6})
    assert result.analysis.scope.months == [f"2026-{m:02}" for m in range(3, 9)]


@pytest.mark.parametrize(
    "period",
    [
        {"kind": "range", "start_month": "2026-08", "end_month": "2026-07"},
        {"kind": "range", "start_month": "2024-01", "end_month": "2026-08"},
        {"kind": "year_to_date"},
    ],
)
def test_invalid_or_uncovered_ranges_cannot_query(period):
    assert normalize(period=period).kind == "clarify"


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "net_profit"},
        {"view": "sales_trend"},
        {"chart": "pie"},
        {"region_name": "华东", "region_unknown": True},
        {"sql": "SELECT * FROM orders"},
        {"period": {"kind": "recent_months", "count": "6"}},
        {"period": {"kind": "last_month", "start_month": "2026-08"}},
        {"period": {"kind": "recent_months"}},
        {"region_id": 1},
    ],
)
def test_model_fields_are_strictly_whitelisted(changes):
    with pytest.raises(ValidationError):
        IntentDraft.model_validate({**DRAFT, **changes})


def test_unsupported_goal_cannot_become_a_sales_query():
    assert normalize(unsupported=True).kind == "unsupported"


def settings():
    return Settings(_env_file=None, llm_api_key="test-private-key")


@pytest.mark.asyncio
async def test_deepseek_json_protocol_and_clarification_input():
    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-private-key"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        user = json.loads(body["messages"][1]["content"])
        assert user["clarification_replies"] == ["改成华南，2026年8月"]
        assert user["reference_date"] == "2026-10-08"
        assert user["allowed_regions"] == ["华东", "华南"]
        properties = user["output_schema"]["properties"]
        assert properties["region_name"]["enum"] == ["华东", "华南", None]
        assert properties["metric"]["anyOf"][0]["enum"] == [
            "amount",
            "count",
            "profit",
            "average",
        ]
        assert properties["view"]["anyOf"][0]["enum"] == [
            "summary",
            "trend",
            "products",
            "regions",
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(DRAFT)}}
                ]
            },
        )

    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        assert (
            await parser.parse(
                "华东销售额",
                ["改成华南，2026年8月"],
                REFERENCE,
                "Asia/Shanghai",
                ("华东", "华南"),
            )
        ).metric == "amount"
    finally:
        await parser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output", [{**DRAFT, "region_name": None}, {**DRAFT, "region_name": "华南"}]
)
async def test_only_regions_mentioned_by_user_are_sent_or_accepted(output):
    def handle(request):
        user = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert user["allowed_regions"] == ["华东"]
        assert user["output_schema"]["properties"]["region_name"]["enum"] == [
            "华东",
            None,
        ]
        assert "华南" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(output)},
                    }
                ]
            },
        )

    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        if output["region_name"] == "华南":
            with pytest.raises(IntentError):
                await parser.parse(
                    "2026年8月华东销售额",
                    [],
                    REFERENCE,
                    "Asia/Shanghai",
                    ("华东", "华南"),
                )
        else:
            assert (
                await parser.parse(
                    "2026年8月华东销售额",
                    [],
                    REFERENCE,
                    "Asia/Shanghai",
                    ("华东", "华南"),
                )
            ).region_name is None
    finally:
        await parser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,finish",
    [
        ("", "stop"),
        ("not json", "stop"),
        (json.dumps({**DRAFT, "sql": "SELECT *"}), "stop"),
        (json.dumps({**DRAFT, "region_name": "模型编造的区域"}), "stop"),
        (json.dumps(DRAFT), "length"),
        (None, "stop"),
    ],
)
async def test_empty_truncated_or_invalid_response_is_not_executed(content, finish):
    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": finish, "message": {"content": content}}
                    ]
                },
            )
        )
    )
    try:
        with pytest.raises(IntentError, match="分析条件无效") as caught:
            await parser.parse("问题", [], REFERENCE, "Asia/Shanghai", ("华东", "华南"))
        assert caught.value.code == "invalid_model_output"
    finally:
        await parser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "model_auth_failed"),
        (429, "model_unavailable"),
        (500, "model_unavailable"),
    ],
)
async def test_model_http_errors_hide_credentials_and_provider_body(status, code):
    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, text="test-private-key internal-error"
            )
        )
    )
    try:
        with pytest.raises(IntentError) as caught:
            await parser.parse("问题", [], REFERENCE, "Asia/Shanghai", ("华东", "华南"))
        assert caught.value.code == code
        assert "test-private-key" not in str(caught.value)
        assert "internal-error" not in str(caught.value)
    finally:
        await parser.close()


@pytest.mark.asyncio
async def test_model_timeout_is_public_and_missing_key_makes_no_request():
    def timeout(request):
        raise httpx.ReadTimeout("test-private-key timeout")

    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(IntentError) as caught:
            await parser.parse("问题", [], REFERENCE, "Asia/Shanghai", ("华东", "华南"))
        assert caught.value.code == "model_timeout"
        parser.settings = Settings(_env_file=None, llm_api_key=None)
        with pytest.raises(IntentError) as caught:
            await parser.parse("问题", [], REFERENCE, "Asia/Shanghai", ("华东", "华南"))
        assert caught.value.code == "model_not_configured"
    finally:
        await parser.close()


def test_followup_changes_only_region_and_standalone_does_not_inherit():
    previous = normalize().analysis
    followup = IntentDraft(region_name="华南", follow_up=True)
    decision = normalize_intent(followup, REFERENCE, CATALOG, previous)
    assert decision.kind == "ready"
    assert decision.analysis.scope.region == 2
    assert decision.analysis.scope.months == previous.scope.months
    assert decision.analysis.metric == previous.metric
    assert "months" in decision.inherited_fields
    standalone = normalize_intent(
        IntentDraft(
            metric="amount",
            metric_text="销售额",
            metric_status="supported",
            region_name="华南",
        ),
        REFERENCE,
        CATALOG,
        previous,
    )
    assert standalone.kind == "clarify" and standalone.fields == ("months",)


def test_explicit_all_regions_overrides_single_region_for_complex_followup():
    previous = normalize().analysis
    draft = IntentDraft(follow_up=True, region_all=True, goal="largest_decline")
    decision = normalize_intent(draft, REFERENCE, CATALOG, previous)
    assert decision.analysis.scope.region == 0
    assert decision.analysis.scope.months == ["2026-08"]
    assert decision.analysis.sort_by == "yoy_percent" and decision.analysis.top_n == 1


def test_followup_without_history_requires_complete_conditions():
    decision = normalize_intent(
        IntentDraft(follow_up=True, region_name="华南"), REFERENCE, CATALOG
    )
    assert decision.kind == "clarify" and decision.fields == ("context",)
    completed = normalize_intent(
        IntentDraft.model_validate({**DRAFT, "follow_up": True}), REFERENCE, CATALOG
    )
    assert completed.kind == "ready"


PRODUCT_CATALOG = replace(
    CATALOG,
    products=(
        ProductItem(id=7, name="商品甲", category="数码"),
        ProductItem(id=8, name="商品乙", category="数码"),
    ),
)


def test_mom_analysis_and_decline_normalize_without_rule_fallback():
    result = normalize(comparison="mom")
    assert result.kind == "ready" and result.analysis.comparison == "mom"
    decline = normalize(
        goal="largest_decline",
        comparison="mom",
        region_name=None,
        region_all=True,
    )
    assert decline.analysis.view == "regions"
    assert decline.analysis.sort_by == "mom_percent"
    assert decline.analysis.sort_order == "asc" and decline.analysis.top_n == 1
    assert "紧邻的前一个等长期间" in SYSTEM_PROMPT
    assert "月趋势环比逐月比较上个月" in SYSTEM_PROMPT
    assert "环比、预测" not in SYSTEM_PROMPT


def test_product_names_resolve_to_ids_and_unknown_never_broadens_scope():
    result = normalize_intent(
        IntentDraft.model_validate(
            {**DRAFT, "view": "products", "product_names": ["商品甲", "商品乙"]}
        ),
        REFERENCE,
        PRODUCT_CATALOG,
    )
    assert result.kind == "ready" and result.analysis.product_ids == [7, 8]
    for changes in ({"product_names": ["火星商品"]}, {"product_unknown": True}):
        unknown = normalize_intent(
            IntentDraft.model_validate({**DRAFT, "view": "products", **changes}),
            REFERENCE,
            PRODUCT_CATALOG,
            result.analysis,
        )
        assert unknown.kind == "clarify" and unknown.analysis is None
        assert unknown.fields == ("products",)


def test_product_followup_inherits_ids_and_explicit_all_clears_selection():
    first = normalize_intent(
        IntentDraft.model_validate(
            {**DRAFT, "view": "products", "product_names": ["商品甲"]}
        ),
        REFERENCE,
        PRODUCT_CATALOG,
    ).analysis
    inherited = normalize_intent(
        IntentDraft(follow_up=True, comparison="mom"),
        REFERENCE,
        PRODUCT_CATALOG,
        first,
    )
    assert (
        inherited.analysis.product_ids == [7]
        and "products" in inherited.inherited_fields
    )
    all_products = normalize_intent(
        IntentDraft(follow_up=True, product_all=True),
        REFERENCE,
        PRODUCT_CATALOG,
        first,
    )
    assert all_products.analysis.product_ids == []
    unavailable = normalize_intent(
        IntentDraft(follow_up=True), REFERENCE, CATALOG, first
    )
    assert unavailable.kind == "clarify" and unavailable.fields == ("products",)


def test_anomaly_template_uses_fixed_month_trend_and_bounded_options():
    result = normalize_intent(
        IntentDraft.model_validate(
            {
                **DRAFT,
                "goal": "anomalies",
                "product_names": ["商品甲"],
                "anomaly_window": 6,
                "anomaly_threshold_percent": 40,
                "comparison": "mom",
                "sort_by": "mom_percent",
                "sort_order": "asc",
                "top_n": 2,
            }
        ),
        REFERENCE,
        PRODUCT_CATALOG,
    )
    assert result.kind == "ready"
    assert result.analysis.goal == "anomalies" and result.analysis.view == "trend"
    assert result.analysis.comparison == "none" and result.analysis.sort_by == "value"
    assert result.analysis.sort_order == "desc" and result.analysis.top_n is None
    assert result.analysis.product_ids == [7]
    assert (
        result.analysis.anomaly_window == 6
        and result.analysis.anomaly_threshold_percent == 40
    )
    assert "统计偏差" in SYSTEM_PROMPT and "绝对百分比偏差超过阈值" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "changes",
    [
        {"product_ids": [7]},
        {"product_names": ["商品甲"], "product_unknown": True},
        {"product_names": ["商品甲"], "product_all": True},
        {"product_names": ["商品甲", "商品甲"]},
        {"product_names": [7]},
        {"anomaly_window": 2},
        {"anomaly_window": 13},
        {"anomaly_threshold_percent": 0},
        {"anomaly_threshold_percent": 1001},
    ],
)
def test_product_and_anomaly_model_fields_reject_invalid_conditions(changes):
    with pytest.raises(ValidationError):
        IntentDraft.model_validate({**DRAFT, **changes})


@pytest.mark.asyncio
@pytest.mark.parametrize("unmentioned", [False, True])
async def test_product_model_candidates_include_only_input_and_confirmed_names(
    unmentioned,
):
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        assert user["allowed_products"] == ["商品甲", "商品乙"]
        properties = user["output_schema"]["properties"]
        assert properties["product_names"]["items"]["enum"] == ["商品甲", "商品乙"]
        assert properties["comparison"]["anyOf"][0]["enum"] == ["none", "yoy", "mom"]
        assert "私密商品" not in request.content.decode()
        output = {
            "follow_up": True,
            "view": "products",
            "product_names": ["私密商品" if unmentioned else "商品乙"],
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(output)},
                    }
                ]
            },
        )

    parser = IntentParser(settings())
    await parser.client.aclose()
    parser.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        arguments = ("商品乙呢", [], REFERENCE, "Asia/Shanghai", ("华东",))
        options = {
            "previous_analysis": {"product_names": ["商品甲"]},
            "allowed_products": ("商品甲", "商品乙", "私密商品"),
        }
        if unmentioned:
            with pytest.raises(IntentError) as caught:
                await parser.parse(*arguments, **options)
            assert caught.value.code == "invalid_model_output"
        else:
            assert (await parser.parse(*arguments, **options)).product_names == [
                "商品乙"
            ]
        assert len(calls) == 1
    finally:
        await parser.close()


@pytest.mark.asyncio
async def test_catalog_products_match_authorized_order_scope(sales_sessions):
    async with sales_sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO sales_data.product VALUES "
                "(2, 'P2', '华南专属商品', '数码', 1, '2026-01-01', '2026-01-01'),"
                "(3, 'P3', '未销售商品', '数码', 1, '2026-01-01', '2026-01-01')"
            )
        )
        await session.execute(
            text(
                "INSERT INTO sales_data.sales_order VALUES "
                "(8, 2, 2, 21, 100, 60, 1, 'COMPLETED', '2026-08-01')"
            )
        )
    database = SimpleNamespace(sales_session=sales_sessions)
    restricted = DataScope("intent-user", (1,), None, ("sales_amount",), 1)
    catalog = await load_intent_catalog(database, restricted)
    assert [p.id for p in catalog.products] == [1]
    assert set(catalog.forbidden_products) == {"华南专属商品", "未销售商品"}
    with pytest.raises(HTTPException) as denied:
        catalog.require_visible_products("比较2026年8月华南专属商品销售额")
    assert denied.value.status_code == 403
    director = await load_intent_catalog(
        database, DataScope("intent-user", None, None, ("sales_amount",), 1)
    )
    assert [p.id for p in director.products] == [1, 2, 3]
