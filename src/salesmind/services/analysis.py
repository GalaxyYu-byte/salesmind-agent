"""白名单分析模板：精确计算、排序、图形数据，模型不参与数值计算。"""

import calendar
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from salesmind.services.conversation_schemas import AnalysisSpec
from salesmind.services.sales_overview import OverviewResponse, month_date, month_number

METRICS = {
    "amount": "销售额",
    "count": "订单数",
    "profit": "毛利",
    "average": "平均客单价",
}
COMPARISONS = {"yoy": ("同比", "去年同期"), "mom": ("环比", "上一期间")}


def shifted_month(month: str, offset: int) -> str:
    number = month_number(date.fromisoformat(month + "-01")) + offset
    return month_date(number).strftime("%Y-%m")


def comparison_months(spec: AnalysisSpec, months: list[str]) -> list[str]:
    offset = (
        -12
        if spec.comparison == "yoy"
        else -1
        if spec.view == "trend"
        else -len(months)
    )
    return [shifted_month(month, offset) for month in months]


def covered_months(overview: OverviewResponse, months: list[str]) -> bool:
    """零订单仍是完整月份；是否覆盖由水位决定，不能以有无聚合行判断。"""
    for month in months:
        start = date.fromisoformat(month + "-01")
        end = date(
            start.year, start.month, calendar.monthrange(start.year, start.month)[1]
        )
        if (
            start < overview.dataset.available_from
            or end > overview.dataset.complete_through
        ):
            return False
    return True


def calculate(spec: AnalysisSpec, overview: OverviewResponse) -> list[dict]:
    if (
        spec.scope.months != overview.months
        or (spec.scope.region or None) != overview.region_id
        or (
            spec.comparison != "none"
            and "comparison" in overview.model_fields_set
            and spec.comparison != overview.comparison
        )
    ):
        raise ValueError("查询结果与已校验条件不一致")
    available_products = {p.id for p in overview.dataset.products}
    if set(spec.product_ids) - available_products:
        raise ValueError("指定商品不存在或不在账号可见范围内")
    selected_products = set(spec.product_ids)
    if spec.view == "regions":
        dimensions = [
            (str(r.id), r.name)
            for r in overview.dataset.regions
            if not spec.scope.region or r.id == spec.scope.region
        ]
    elif spec.view == "products":
        dimensions = [
            (str(p.id), p.name)
            for p in overview.dataset.products
            if not selected_products or p.id in selected_products
        ]
    elif spec.view == "trend":
        dimensions = [(m, m) for m in overview.months]
    else:
        dimensions = [("total", "当前期间")]

    def aggregate(months, key):
        cells = [
            c
            for c in overview.dataset.cells
            if c.month in months
            and (not spec.scope.region or c.region == spec.scope.region)
            and (not selected_products or c.product in selected_products)
            and (spec.view != "regions" or str(c.region) == key)
            and (spec.view != "products" or str(c.product) == key)
        ]
        if spec.metric == "profit":
            # 毛利单独返回；成本未授权时不依赖原始成本列。
            values = [
                Decimal(c.gross_profit)
                if c.gross_profit is not None
                else Decimal(c.amount) - Decimal(c.cost)
                if c.amount is not None and c.cost is not None
                else None
                for c in cells
            ]
            if any(value is None for value in values):
                raise ValueError("查询结果缺少已授权的毛利指标")
            return sum(values, Decimal(0)) / 100
        if spec.metric in {"amount", "average"}:
            if any(c.amount is None for c in cells):
                raise ValueError("查询结果缺少已授权的销售额指标")
            amount = sum((Decimal(c.amount) for c in cells), Decimal(0)) / 100
            if spec.metric == "amount":
                return amount
        if any(c.count is None for c in cells):
            raise ValueError("查询结果缺少已授权的订单数指标")
        count = sum(c.count for c in cells)
        return (
            Decimal(count)
            if spec.metric == "count"
            else amount / count
            if count
            else None
        )

    rows = []
    growth_allowed = (
        overview.access is None or "growth_rate" in overview.access.allowed_metrics
    )
    for key, name in dimensions:
        months = [key] if spec.view == "trend" else overview.months
        current = aggregate(months, key)
        baseline, change, percent, expected, deviation = None, None, None, None, None
        reason, anomaly = "", False
        if spec.goal == "anomalies":
            prior = [
                shifted_month(key, -offset)
                for offset in range(spec.anomaly_window, 0, -1)
            ]
            fetched = set(overview.history_months) | set(overview.months)
            if (
                not growth_allowed
                or not set(prior) <= fetched
                or not covered_months(overview, prior)
            ):
                reason = f"前 {spec.anomaly_window} 个月未完整覆盖，无法判断异常"
            else:
                expected = aggregate(prior, key)
                # 客单价按历史总额 / 总订单加权；金额、订单数和毛利取月均。
                if expected is not None and spec.metric != "average":
                    expected /= spec.anomaly_window
                baseline = expected
                if expected is None or current is None:
                    reason = "期间无订单，客单价不可计算"
                elif expected <= 0:
                    reason = "历史均值为零或负数，无法判断异常"
                else:
                    deviation = (current - expected) / expected * 100
                    anomaly = abs(deviation) >= Decimal(
                        str(spec.anomaly_threshold_percent)
                    )
        elif spec.comparison != "none":
            prior = comparison_months(spec, months)
            fetched = set(overview.baseline_months) | set(overview.months)
            covered = (
                growth_allowed
                and set(prior) <= fetched
                and covered_months(overview, prior)
                and (spec.view == "trend" or overview.baseline_covered)
            )
            label = COMPARISONS[spec.comparison][1]
            if not covered:
                reason = f"{label}未完整覆盖"
            else:
                baseline = aggregate(prior, key)
                if current is None or baseline is None:
                    reason = "期间无订单，客单价不可计算"
                elif baseline <= 0:
                    reason = f"{label}为零或负数，增长率不可比"
                change = (
                    current - baseline
                    if current is not None and baseline is not None
                    else None
                )
                percent = change / baseline * 100 if not reason else None
        elif current is None:
            reason = "期间无订单，客单价不可计算"
        rows.append(
            {
                "id": key,
                "name": name,
                "current": current,
                "baseline": baseline,
                "comparison_change": change,
                "comparison_percent": percent,
                "yoy_change": change if spec.comparison == "yoy" else None,
                "yoy_percent": percent if spec.comparison == "yoy" else None,
                "mom_change": change if spec.comparison == "mom" else None,
                "mom_percent": percent if spec.comparison == "mom" else None,
                "anomaly": anomaly,
                "expected": expected,
                "deviation_percent": deviation,
                "reason": reason,
            }
        )
    return rows


def rank(spec: AnalysisSpec, rows: list[dict]) -> dict:
    key = "current" if spec.sort_by == "value" else spec.sort_by
    if spec.view in {"regions", "products"}:
        comparable = sorted(
            (r for r in rows if r[key] is not None), key=lambda r: r["id"]
        )
        comparable.sort(key=lambda r: r[key], reverse=spec.sort_order == "desc")
        ordered = comparable + [r for r in rows if r[key] is None]
    else:
        comparable, ordered = rows, rows
    if spec.goal == "largest_decline":
        candidates = [
            r
            for r in comparable
            if r["comparison_percent"] is not None and r["comparison_percent"] < 0
        ]
    elif spec.goal == "anomalies":
        candidates = [r for r in rows if r["anomaly"]]
    else:
        candidates = comparable
    selected = candidates[: spec.top_n] if spec.top_n else candidates
    return {"rows": ordered, "selected_ids": [r["id"] for r in selected]}


def visualize(
    spec: AnalysisSpec, ranked: dict, overview: OverviewResponse | None = None
) -> dict:
    rows = ranked["rows"]
    # 下降最多与异常检测保留全部维度，便于检查结论。
    plotted = (
        rows
        if spec.goal in {"largest_decline", "anomalies"} or not spec.top_n
        else [r for r in rows if r["id"] in ranked["selected_ids"]]
    )
    comparison_label, baseline_label = COMPARISONS.get(spec.comparison, ("", ""))
    if spec.comparison == "mom" and spec.view == "trend":
        baseline_label = "上一月"
    if spec.goal == "anomalies":
        baseline_label = f"前 {spec.anomaly_window} 个月历史均值"
    percent_chart = spec.comparison != "none" and spec.view in {"regions", "products"}
    unit = "%" if percent_chart else "笔" if spec.metric == "count" else "元"
    value_key = "comparison_percent" if percent_chart else "current"
    selected = set(ranked["selected_ids"])
    trace = {
        "x": [r["name"] for r in plotted],
        "y": [
            float(r[value_key]) if r[value_key] is not None else None for r in plotted
        ],
        "type": "bar" if spec.chart == "bar" else "scatter",
        "mode": "lines+markers",
        "name": f"{comparison_label}增长率" if percent_chart else "当前期间",
        "marker": {
            "color": [
                "#a95745"
                if spec.goal in {"largest_decline", "anomalies"} and r["id"] in selected
                else "#2f6851"
                for r in plotted
            ]
        },
        "hovertemplate": "%{x}<br>%{y:,.2f}" + unit + "<extra></extra>",
    }
    traces = [trace]
    if (spec.comparison != "none" and not percent_chart) or spec.goal == "anomalies":
        traces.append(
            {
                **trace,
                "y": [
                    float(r["baseline"]) if r["baseline"] is not None else None
                    for r in plotted
                ],
                "name": baseline_label,
                "marker": {"color": "#b8c3bb"},
            }
        )
    name = METRICS[spec.metric]
    first = next((r for r in rows if r["id"] in selected), None)
    if spec.goal == "anomalies":
        usable = sum(r["deviation_percent"] is not None for r in rows)
        headline = (
            f"发现 {len(selected)} 个{name}异常月份："
            + "、".join(r["name"] for r in rows if r["anomaly"])
            + "。"
            if selected
            else f"可判断的 {usable} 个月中，没有达到阈值的{name}异常。"
            if usable
            else "历史窗口未覆盖或均值不可比，暂时无法判断异常。"
        )
    elif spec.goal == "largest_decline":
        if first:
            headline = f"{first['name']}的{name}{comparison_label}下降最多，下降 {abs(first['comparison_percent']):.2f}%。"
        elif any(r["comparison_percent"] is not None for r in rows):
            headline = f"可比较的区域中，没有{name}{comparison_label}下降的区域。"
        else:
            headline = f"没有可计算{comparison_label}下降百分比的区域，暂时无法判断下降最多的区域。"
    elif first and spec.view in {"regions", "products"}:
        basis = (
            f"{comparison_label}增长率 {first['comparison_percent']:.2f}%"
            if spec.sort_by.endswith("_percent")
            else f"{comparison_label}增减 {first['comparison_change']:.2f}"
            if spec.sort_by.endswith("_change")
            else f"{name} {first['current']:.2f}"
        )
        headline = f"按{'升序' if spec.sort_order == 'asc' else '降序'}排列，{first['name']}位列第一，{basis}。"
    else:
        if spec.view == "trend" and spec.comparison == "mom":
            summary = rows[-1] if rows else None
            prefix = f"{summary['name']}的" if summary else ""
        else:
            summary_rows = (
                calculate(spec.model_copy(update={"view": "summary"}), overview)
                if overview
                else rows
            )
            summary = summary_rows[0] if summary_rows else None
            prefix = ""
        if summary and summary["current"] is not None:
            headline = f"{prefix}{name}为 {summary['current']:.2f} {'笔' if spec.metric == 'count' else '元'}。"
            if spec.comparison != "none":
                headline += (
                    f"{comparison_label}{'下降' if summary['comparison_percent'] < 0 else '增长'} {abs(summary['comparison_percent']):.2f}%。"
                    if summary["comparison_percent"] is not None
                    else f"{summary['reason']}。"
                )
        else:
            headline = f"当前范围没有可计算的{name}。"
    excluded = sum(r["comparison_percent"] is None for r in rows)
    if spec.goal == "anomalies":
        excluded = sum(r["deviation_percent"] is None for r in rows)
        aggregation = (
            "历史总销售额 ÷ 历史总订单数"
            if spec.metric == "average"
            else "历史指标总和 ÷ 窗口月数"
        )
        detail = (
            f"异常口径：每月与此前 {spec.anomaly_window} 个完整月比较，历史均值 = {aggregation}；"
            f"绝对偏离百分比达到 {spec.anomaly_threshold_percent:g}% 标记异常。不可判断月份 {excluded} 个。"
        )
        caveat = "基于 MySQL 查询快照与历史窗口识别数值异常；异常标记不代表原因或预测。"
    elif spec.comparison != "none":
        detail = f"比较口径：{comparison_label}百分比 =（本期 − {baseline_label}）÷ {baseline_label} × 100%。不可比项 {excluded} 个。"
        if spec.comparison == "mom" and spec.view != "trend":
            detail += "上一期间为紧邻当前期间、月数相同的完整月份。"
        caveat = (
            f"基于 MySQL 查询快照计算；不可比项不参与{comparison_label}百分比排名。"
        )
    else:
        detail = "按已完成订单统计；退款和取消订单已排除。"
        caveat = "基于 MySQL 查询快照计算。"
    if overview:
        if not spec.product_ids:
            if overview.totals.order_count is not None:
                detail += f"统计 {overview.totals.order_count} 笔已完成订单。"
            if overview.totals.gross_profit is not None:
                detail += f"总毛利 {overview.totals.gross_profit} 元。"
        if overview.access:
            detail += f"可见范围：{overview.access.scope_label}。"
    return {
        "rows": [
            {k: str(v) if isinstance(v, Decimal) else v for k, v in r.items()}
            for r in rows
        ],
        "selected_ids": ranked["selected_ids"],
        "sort_by": spec.sort_by,
        "sort_order": spec.sort_order,
        "comparison": spec.comparison,
        "baseline_label": baseline_label or None,
        "headline": headline,
        "detail": detail,
        "caveat": caveat,
        "figure": {
            "data": traces,
            "layout": {
                "yaxis": {
                    "ticksuffix": unit,
                    "title": {
                        "text": f"{comparison_label}增长率" if percent_chart else name
                    },
                },
                "showlegend": len(traces) > 1,
            },
        },
        "chart_unit": unit,
    }


class AnalysisRow(BaseModel):
    id: str
    name: str
    current: str | None
    baseline: str | None
    yoy_change: str | None = None
    yoy_percent: str | None = None
    comparison_change: str | None = None
    comparison_percent: str | None = None
    mom_change: str | None = None
    mom_percent: str | None = None
    anomaly: bool = False
    expected: str | None = None
    deviation_percent: str | None = None
    reason: str


class AnalysisResult(BaseModel):
    rows: list[AnalysisRow]
    selected_ids: list[str]
    sort_by: str
    sort_order: str
    comparison: str | None = None
    baseline_label: str | None = None
    headline: str
    detail: str
    caveat: str
    figure: dict
    chart_unit: str
