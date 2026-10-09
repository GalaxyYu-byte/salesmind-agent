"""精确同比计算与模板执行结果，不依赖外部模型或业务数据库。"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from salesmind.services.analysis import AnalysisResult, calculate, rank, visualize
from salesmind.services.conversation_schemas import AnalysisSpec
from salesmind.services.sales_overview import OverviewResponse


def overview(
    current=(900, 40, 15), baseline=(1000, 100, 0), *, covered=True, counts=None
):
    cells = []
    for month, amounts in [("2026-08", current), ("2025-08", baseline)]:
        for i, amount in enumerate(amounts, 1):
            cells.append(
                {
                    "month": month,
                    "region": i,
                    "product": 1,
                    "amount": str(amount * 100),
                    "cost": "0",
                    "count": (counts or [1] * len(amounts))[i - 1],
                    "quantity": 1,
                }
            )
    return OverviewResponse.model_validate(
        {
            "queried_at": datetime.now(UTC),
            "months": ["2026-08"],
            "region_id": None,
            "baseline_months": ["2025-08"],
            "baseline_covered": covered,
            "totals": {
                "sales_amount": f"{sum(current):.2f}",
                "gross_profit": f"{sum(current):.2f}",
                "order_count": sum(counts or [1] * len(current)),
                "quantity": len(current),
            },
            "dataset": {
                "version": 1,
                "available_from": date(2024, 2, 1),
                "complete_through": date(2026, 8, 31),
                "order_count": len(cells),
                "regions": [
                    {"id": i, "name": name}
                    for i, name in enumerate(
                        ["华东", "华南", "华北"][: len(current)], 1
                    )
                ],
                "products": [{"id": 1, "name": "商品", "category": "数码"}],
                "cells": cells,
            },
        }
    )


def spec(**kwargs):
    return AnalysisSpec.model_validate(
        {
            "scope": {"months": ["2026-08"], "region": 0},
            "metric": "amount",
            "view": "regions",
            "chart": "bar",
            "comparison": "yoy",
            "sort_by": "yoy_percent",
            "sort_order": "asc",
            "goal": "largest_decline",
            **kwargs,
        }
    )


def test_decline_uses_percentage_not_amount_and_plots_all_regions():
    data, plan = overview(), spec()
    rows = calculate(plan, data)
    assert rows[0]["yoy_change"] == Decimal(-100)
    assert rows[1]["yoy_change"] == Decimal(-60)
    ranked = rank(plan, rows)
    assert ranked["selected_ids"] == ["2"]
    result = visualize(plan, ranked, data)
    assert result["figure"]["data"][0]["y"] == [-60, -10, None]
    assert result["figure"]["layout"]["yaxis"]["ticksuffix"] == "%"
    assert "华南" in result["headline"] and "60.00%" in result["headline"]
    assert result["rows"][-1]["reason"]


@pytest.mark.parametrize(
    "covered,baseline", [(False, (1000, 100, 10)), (True, (0, 0, 0))]
)
def test_missing_or_zero_baseline_cannot_produce_winner(covered, baseline):
    data, plan = overview(baseline=baseline, covered=covered), spec()
    result = visualize(plan, rank(plan, calculate(plan, data)), data)
    assert result["selected_ids"] == []
    assert "无法判断" in result["headline"]
    assert all(r["yoy_percent"] is None for r in result["rows"])


def test_all_regions_growing_has_no_decline_winner():
    data, plan = overview(current=(1100, 101, 15), baseline=(1000, 100, 10)), spec()
    result = visualize(plan, rank(plan, calculate(plan, data)), data)
    assert result["selected_ids"] == [] and "没有销售额同比下降" in result["headline"]


def test_current_zero_is_valid_full_decline_and_ties_are_stable():
    data, plan = overview(current=(0, 0, 15), baseline=(1000, 100, 0)), spec()
    ranked = rank(plan, calculate(plan, data))
    assert ranked["selected_ids"] == ["1"] and ranked["rows"][0]["yoy_percent"] == -100


def test_average_uses_weighted_totals_and_zero_orders_are_undefined():
    data = overview(current=(900, 100), baseline=(800, 200), counts=[9, 1])
    plan = spec(metric="average", view="summary", sort_by="value", goal="overview")
    row = calculate(plan, data)[0]
    assert row["current"] == row["baseline"] == 100
    data = overview(current=(0, 100), baseline=(0, 200), counts=[0, 1])
    plan = spec(metric="average")
    assert calculate(plan, data)[0]["current"] is None


def test_negative_profit_baseline_is_not_ranked_as_growth():
    data = overview()
    for cell in data.dataset.cells:
        if cell.month == "2025-08":
            cell.cost = str(int(cell.amount) + 100)
    plan = spec(metric="profit")
    assert rank(plan, calculate(plan, data))["selected_ids"] == []


def test_top_n_and_unrounded_percentage_order():
    plan = spec(goal="overview", top_n=2)
    data = overview(current=(999, 998, 999), baseline=(1000, 1000, 1000))
    ranked = rank(plan, calculate(plan, data))
    assert ranked["selected_ids"] == ["2", "1"]
    assert len(visualize(plan, ranked)["figure"]["data"][0]["x"]) == 2


def test_scope_mismatch_is_rejected():
    data = overview()
    data.region_id = 1
    with pytest.raises(ValueError):
        calculate(spec(), data)


def series(
    values,
    months,
    *,
    comparison="none",
    baseline_months=None,
    history_months=None,
    counts=None,
    available="2024-01-01",
    covered=True,
):
    payload = overview(current=(1,), baseline=(1,)).model_dump()
    payload.update(
        months=months,
        comparison=comparison,
        baseline_months=baseline_months or [],
        history_months=history_months or [],
        baseline_covered=covered,
    )
    payload["dataset"].update(
        available_from=available,
        complete_through="2026-08-31",
        cells=[
            {
                "month": month,
                "region": 1,
                "product": 1,
                "amount": str(amount * 100),
                "cost": "0",
                "count": (counts or {}).get(month, 1),
                "quantity": 1,
            }
            for month, amount in values.items()
        ],
    )
    return OverviewResponse.model_validate(payload)


def template(months, **changes):
    return AnalysisSpec.model_validate(
        {
            "scope": {"months": months, "region": 0},
            "view": "trend",
            **changes,
        }
    )


def test_mom_period_uses_equal_length_previous_period_across_year():
    data = series(
        {"2025-11": 50, "2025-12": 150, "2026-01": 100, "2026-02": 200},
        ["2026-01", "2026-02"],
        comparison="mom",
        baseline_months=["2025-11", "2025-12"],
    )
    plan = template(data.months, view="summary", comparison="mom")
    row = calculate(plan, data)[0]
    assert row["current"] == 300 and row["baseline"] == 200
    assert row["mom_change"] == row["comparison_change"] == 100
    assert row["mom_percent"] == row["comparison_percent"] == 50
    assert row["yoy_percent"] is None
    result = visualize(plan, rank(plan, [row]), data)
    assert "环比增长 50.00%" in result["headline"]
    assert "月数相同" in result["detail"]
    assert result["figure"]["data"][1]["name"] == "上一期间"
    query = plan.overview_query()
    assert query.comparison == "mom" and query.baseline_mode == "period"


def test_mom_trend_compares_each_month_to_its_immediate_predecessor():
    data = series(
        {"2025-12": 50, "2026-01": 100, "2026-02": 150},
        ["2026-01", "2026-02"],
        comparison="mom",
        baseline_months=["2025-12", "2026-01"],
    )
    plan = template(data.months, comparison="mom")
    rows = calculate(plan, data)
    assert [r["mom_percent"] for r in rows] == [100, 50]
    result = visualize(plan, rank(plan, rows), data)
    assert "2026-02" in result["headline"] and "环比增长 50.00%" in result["headline"]
    assert result["baseline_label"] == "上一月"
    assert result["figure"]["data"][1]["y"] == [50, 100]
    assert plan.overview_query().baseline_mode == "month"


def test_trend_comparison_evaluates_partial_baseline_coverage_per_month():
    data = series(
        {"2026-01": 100, "2026-02": 200},
        ["2026-01", "2026-02"],
        comparison="mom",
        baseline_months=["2025-12", "2026-01"],
        available="2026-01-01",
        covered=False,
    )
    rows = calculate(template(data.months, comparison="mom"), data)
    assert rows[0]["mom_percent"] is None and rows[0]["reason"]
    assert rows[1]["mom_percent"] == 100


def test_mom_decline_ranking_and_chart_labels():
    data = overview()
    data.comparison = "mom"
    data.baseline_months = ["2026-07"]
    for cell in data.dataset.cells:
        if cell.month == "2025-08":
            cell.month = "2026-07"
    plan = spec(comparison="mom", sort_by="mom_percent")
    result = visualize(plan, rank(plan, calculate(plan, data)), data)
    assert result["selected_ids"] == ["2"]
    assert "环比下降最多" in result["headline"] and "同比" not in result["headline"]
    assert result["figure"]["data"][0]["name"] == "环比增长率"
    assert "环比" in result["caveat"]


def test_comparison_none_does_not_compute_hidden_growth_fields():
    data = series({"2026-08": 100}, ["2026-08"])
    row = calculate(template(data.months), data)[0]
    assert row["current"] == 100
    assert row["baseline"] is row["yoy_percent"] is row["comparison_percent"] is None
    assert row["reason"] == ""


def test_explicit_snapshot_comparison_mismatch_is_rejected():
    data = series({"2026-08": 100}, ["2026-08"], comparison="yoy")
    with pytest.raises(ValueError, match="不一致"):
        calculate(template(data.months, comparison="mom"), data)


def test_selected_product_comparison_filters_both_periods_and_unknown_ids():
    data = series(
        {"2026-08": 200, "2026-07": 100},
        ["2026-08"],
        comparison="mom",
        baseline_months=["2026-07"],
    )
    payload = data.model_dump()
    payload["dataset"]["products"] += [
        {"id": 2, "name": "第二商品", "category": "数码"},
        {"id": 3, "name": "未选择商品", "category": "数码"},
    ]
    payload["dataset"]["cells"] += [
        {
            "month": month,
            "region": 1,
            "product": product,
            "amount": str(amount * 100),
            "count": 1,
        }
        for product, current, prior in [(2, 400, 100), (3, 9999, 1)]
        for month, amount in [("2026-08", current), ("2026-07", prior)]
    ]
    data = OverviewResponse.model_validate(payload)
    plan = template(
        data.months,
        view="products",
        comparison="mom",
        product_ids=[1, 2],
        sort_by="mom_percent",
    )
    result = visualize(plan, rank(plan, calculate(plan, data)), data)
    assert [r["id"] for r in result["rows"]] == ["2", "1"]
    assert [r["mom_percent"] for r in result["rows"]] == ["300", "100"]
    assert result["figure"]["data"][0]["x"] == ["第二商品", "商品"]
    assert "总毛利" not in result["detail"]
    with pytest.raises(ValueError, match="商品不存在"):
        calculate(
            template(data.months, view="products", comparison="mom", product_ids=[999]),
            data,
        )


@pytest.mark.parametrize("ids", [[0], [-1], [True], ["1"], [1, 1], list(range(1, 22))])
def test_product_ids_are_strict_positive_distinct_and_bounded(ids):
    with pytest.raises(ValidationError):
        template(["2026-08"], view="products", product_ids=ids)


@pytest.mark.parametrize(
    "changes",
    [
        {"view": "summary"},
        {"comparison": "mom"},
        {"sort_by": "mom_percent"},
        {"sort_order": "asc"},
        {"top_n": 1},
        {"anomaly_window": 2},
        {"anomaly_window": 13},
        {"anomaly_window": True},
        {"anomaly_threshold_percent": 0},
        {"anomaly_threshold_percent": 1001},
    ],
)
def test_anomaly_template_rejects_incompatible_or_unbounded_parameters(changes):
    with pytest.raises(ValidationError):
        template(["2026-08"], goal="anomalies", **changes)


def test_growth_sort_must_match_comparison_and_dimension():
    with pytest.raises(ValidationError):
        template(["2026-08"], view="products", comparison="yoy", sort_by="mom_percent")
    with pytest.raises(ValidationError):
        template(["2026-08"], comparison="mom", sort_by="mom_percent")
    with pytest.raises(ValidationError):
        template(["2026-08"], product_ids=[1])


def test_anomalies_mark_inclusive_threshold_and_keep_every_trend_point():
    data = series(
        {
            "2026-04": 100,
            "2026-05": 100,
            "2026-06": 100,
            "2026-07": 130,
            "2026-08": 110,
        },
        ["2026-07", "2026-08"],
        history_months=["2026-04", "2026-05", "2026-06"],
    )
    plan = template(data.months, goal="anomalies")
    rows = calculate(plan, data)
    assert rows[0]["expected"] == 100 and rows[0]["deviation_percent"] == 30
    assert rows[0]["anomaly"] and not rows[1]["anomaly"]
    assert rows[1]["expected"] == 110 and rows[1]["deviation_percent"] == 0
    result = visualize(plan, rank(plan, rows), data)
    assert result["selected_ids"] == ["2026-07"]
    assert result["figure"]["data"][0]["x"] == data.months
    assert result["figure"]["data"][1]["y"] == [100, 110]
    assert "异常月份" in result["headline"] and "不代表原因" in result["caveat"]
    assert plan.overview_query().history_months == 3
    assert plan.overview_query().comparison == "none"


def test_anomaly_current_zero_is_valid_and_covered_zero_order_month_is_included():
    data = series(
        {"2026-05": 100, "2026-06": 200, "2026-08": 0},
        ["2026-08"],
        history_months=["2026-05", "2026-06", "2026-07"],
    )
    row = calculate(template(data.months, goal="anomalies"), data)[0]
    assert row["expected"] == 100 and row["deviation_percent"] == -100
    assert row["anomaly"]


def test_anomaly_average_uses_weighted_historical_orders():
    data = series(
        {"2026-05": 900, "2026-06": 100, "2026-07": 0, "2026-08": 150},
        ["2026-08"],
        history_months=["2026-05", "2026-06", "2026-07"],
        counts={"2026-05": 9, "2026-06": 1, "2026-07": 0},
    )
    row = calculate(template(data.months, goal="anomalies", metric="average"), data)[0]
    assert row["expected"] == 100 and row["deviation_percent"] == 50 and row["anomaly"]


@pytest.mark.parametrize("historical", [0, -100])
def test_zero_or_negative_historical_mean_is_not_an_anomaly(historical):
    data = series(
        {
            "2026-05": historical,
            "2026-06": historical,
            "2026-07": historical,
            "2026-08": 100,
        },
        ["2026-08"],
        history_months=["2026-05", "2026-06", "2026-07"],
    )
    plan = template(data.months, goal="anomalies", metric="profit")
    row = calculate(plan, data)[0]
    assert row["deviation_percent"] is None and not row["anomaly"]
    assert "零或负数" in row["reason"]
    assert rank(plan, [row])["selected_ids"] == []


def test_anomaly_coverage_is_checked_for_each_rolling_window():
    data = series(
        {"2026-05": 100, "2026-06": 100, "2026-07": 100, "2026-08": 200},
        ["2026-05", "2026-06", "2026-07", "2026-08"],
        history_months=["2026-02", "2026-03", "2026-04"],
        available="2026-05-01",
    )
    rows = calculate(template(data.months, goal="anomalies"), data)
    assert all(r["deviation_percent"] is None for r in rows[:3])
    assert rows[3]["deviation_percent"] == 100 and rows[3]["anomaly"]
    data.history_months = []
    assert (
        calculate(template(data.months, goal="anomalies"), data)[0]["expected"] is None
    )


def test_unfetched_history_cannot_be_interpreted_as_zero_sales():
    data = series({"2026-08": 100}, ["2026-08"])
    row = calculate(template(data.months, goal="anomalies"), data)[0]
    assert row["expected"] is None and "未完整覆盖" in row["reason"]
    assert not row["anomaly"]


def test_selected_product_anomaly_ignores_unselected_sales():
    data = series(
        {"2026-05": 100, "2026-06": 100, "2026-07": 100, "2026-08": 100},
        ["2026-08"],
        history_months=["2026-05", "2026-06", "2026-07"],
    )
    payload = data.model_dump()
    payload["dataset"]["products"].append(
        {"id": 2, "name": "第二商品", "category": "数码"}
    )
    payload["dataset"]["cells"].append(
        {
            "month": "2026-08",
            "region": 1,
            "product": 2,
            "amount": "10000000",
            "count": 1,
        }
    )
    data = OverviewResponse.model_validate(payload)
    row = calculate(template(data.months, goal="anomalies", product_ids=[1]), data)[0]
    assert (
        row["current"] == 100 and row["deviation_percent"] == 0 and not row["anomaly"]
    )


def test_new_result_fields_are_optional_for_saved_legacy_results():
    payload = visualize(spec(), rank(spec(), calculate(spec(), overview())), overview())
    payload.pop("comparison")
    payload.pop("baseline_label")
    for row in payload["rows"]:
        for key in [
            "comparison_change",
            "comparison_percent",
            "mom_change",
            "mom_percent",
            "anomaly",
            "expected",
            "deviation_percent",
        ]:
            row.pop(key)
    legacy = AnalysisResult.model_validate(payload)
    assert legacy.comparison is None and legacy.baseline_label is None
    assert legacy.rows[0].anomaly is False and legacy.rows[0].mom_percent is None


def test_profit_only_analysis_does_not_require_or_expose_cost_and_amount():
    data = overview(current=(900, 40), baseline=(1000, 100))
    for cell in data.dataset.cells:
        cell.gross_profit = cell.amount
        cell.amount = cell.cost = None
    data.totals.sales_amount = None
    data.totals.gross_profit = "940.00"
    plan = spec(metric="profit")
    rows = calculate(plan, data)
    assert rows[0]["current"] == 900
    assert rows[0]["baseline"] == 1000
    assert "940.00" in visualize(plan, rank(plan, rows), data)["detail"]


def test_count_only_analysis_omits_unauthorized_monetary_summary():
    data = overview(current=(900, 40), baseline=(1000, 100))
    for cell in data.dataset.cells:
        cell.amount = cell.cost = cell.gross_profit = None
    data.totals.sales_amount = data.totals.gross_profit = None
    plan = spec(metric="count", sort_by="value", goal="overview")
    rows = calculate(plan, data)
    assert rows[0]["current"] == 1
    result = visualize(plan, rank(plan, rows), data)
    assert "毛利" not in result["detail"]
    assert "None" not in result["detail"]


def test_missing_authorized_metric_is_rejected_before_calculation():
    data = overview()
    data.dataset.cells[0].amount = None
    with pytest.raises(ValueError, match="销售额"):
        calculate(spec(), data)
