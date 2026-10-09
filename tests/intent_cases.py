"""固定日期与目录的十组意图评测；gold_draft 仅用于离线查询阻断回归。"""

from datetime import date

from salesmind.services.intent import IntentCatalog
from salesmind.services.sales_overview import ProductItem, RegionItem

REFERENCE = date(2026, 10, 8)
CATALOG = IntentCatalog(
    date(2024, 2, 1),
    date(2026, 9, 21),
    (RegionItem(id=1, name="华东"), RegionItem(id=2, name="华南")),
    products=(
        ProductItem(id=1, name="商品甲", category="数码"),
        ProductItem(id=2, name="商品乙", category="数码"),
    ),
)


def period(start="2026-08", end=None):
    return {"kind": "range", "start_month": start, "end_month": end or start}


def draft(**changes):
    metric = changes.get("metric", "amount")
    return {
        "metric": "amount",
        "metric_text": {
            "amount": "销售额",
            "count": "订单数",
            "profit": "毛利",
            "average": "平均客单价",
        }[metric],
        "metric_status": "supported",
        "period": period(),
        "region_name": "华东",
        **changes,
    }


def spec(metric="amount", months=None, region=1, view="summary", **changes):
    return {
        "scope": {"months": months or ["2026-08"], "region": region},
        "metric": metric,
        "view": view,
        "chart": "line" if view == "trend" else "bar",
        "comparison": "none",
        "sort_by": "value",
        "sort_order": "desc",
        "top_n": None,
        "goal": "overview",
        "product_ids": [],
        "anomaly_window": 3,
        "anomaly_threshold_percent": 30,
        **changes,
    }


def turn(text, gold, *, reply=False, analysis=None, fields=None, inherited=None):
    status = "completed" if analysis else "awaiting_input" if fields else "failed"
    return {
        "text": text,
        "action": "reply" if reply else "question",
        "gold_draft": gold,
        "expected": {
            "status": status,
            "analysis": analysis,
            "fields": fields or [],
            "error_code": "unsupported" if status == "failed" else None,
            "query_delta": int(status == "completed"),
            "inherited": inherited or [],
        },
    }


CASES = [
    {
        "id": "01-complete",
        "name": "完整问句：销售额月趋势",
        "turns": [
            turn(
                "2026年8月华东销售额走势如何？",
                draft(view="trend"),
                analysis=spec(view="trend"),
            )
        ],
    },
    {
        "id": "02-missing-time",
        "name": "缺少时间，补齐后查询",
        "turns": [
            turn(
                "华东卖了多少钱？",
                draft(period=None, metric_text="卖了多少钱"),
                fields=["months"],
            ),
            turn(
                "2026年8月",
                draft(metric_text="卖了多少钱"),
                reply=True,
                analysis=spec(),
            ),
        ],
    },
    {
        "id": "03-multiple-corrections",
        "name": "连续澄清，最后更正区域、指标、时间",
        "turns": [
            turn(
                "看看华东",
                {"region_name": "华东", "metric": None, "period": None},
                fields=["metric", "months"],
            ),
            turn("按销售额统计", draft(period=None), reply=True, fields=["months"]),
            turn(
                "不看华东了，改成2026年7月华南订单数",
                draft(metric="count", period=period("2026-07"), region_name="华南"),
                reply=True,
                analysis=spec("count", ["2026-07"], 2),
            ),
        ],
    },
    {
        "id": "04-correct-metric",
        "name": "模糊时间不能查询，更正指标后继续追问",
        "turns": [
            turn(
                "最近华东销售额走势如何？",
                draft(period=None, view="trend"),
                fields=["months"],
            ),
            turn(
                "不是销售额，改看毛利",
                draft(metric="profit", period=None, view="trend"),
                reply=True,
                fields=["months"],
            ),
            turn(
                "2026年7月至8月",
                draft(
                    metric="profit", period=period("2026-07", "2026-08"), view="trend"
                ),
                reply=True,
                analysis=spec("profit", ["2026-07", "2026-08"], view="trend"),
            ),
        ],
    },
    {
        "id": "05-region-followup",
        "name": "区域追问继承已确认月份、指标、视图",
        "turns": [
            turn(
                "2026年8月华东平均客单价走势",
                draft(metric="average", view="trend"),
                analysis=spec("average", view="trend"),
            ),
            turn(
                "华南呢？",
                {"follow_up": True, "region_name": "华南"},
                analysis=spec("average", region=2, view="trend"),
                inherited=["metric", "months", "view", "chart", "comparison"],
            ),
        ],
    },
    {
        "id": "06-no-history",
        "name": "新会话区域追问不能跨会话继承",
        "turns": [
            turn(
                "华南呢？",
                {"follow_up": True, "region_name": "华南"},
                fields=["context"],
            ),
            turn(
                "看2026年8月华南订单数汇总",
                draft(metric="count", region_name="华南"),
                reply=True,
                analysis=spec("count", region=2),
            ),
        ],
    },
    {
        "id": "07-unknown-region",
        "name": "未知区域应澄清，不能改成全部区域",
        "turns": [
            turn(
                "2026年8月火星区销售额多少？",
                draft(region_name=None, region_unknown=True),
                fields=["region"],
            ),
            turn(
                "区域改成华南",
                draft(region_name="华南"),
                reply=True,
                analysis=spec(region=2),
            ),
        ],
    },
    {
        "id": "08-unknown-metric",
        "name": "净利润不等于毛利，未知指标停止查询",
        "turns": [turn("2026年8月华东净利润多少？", {"unsupported": True})],
    },
    {
        "id": "09-unknown-followup",
        "name": "未知指标追问不能沿用上一轮销售额",
        "turns": [
            turn("2026年8月华东销售额多少？", draft(), analysis=spec()),
            turn("改看转化率呢？", {"unsupported": True}),
        ],
    },
    {
        "id": "10-relative-watermark",
        "name": "上个月按日历解析，水位不足时追问",
        "turns": [
            turn(
                "上个月华东销售额多少？",
                draft(period={"kind": "last_month"}),
                fields=["months"],
            ),
            turn("那改成2026年8月", draft(), reply=True, analysis=spec()),
        ],
    },
    {
        "id": "11-mom-summary",
        "name": "完整问句：销售额环比汇总",
        "turns": [
            turn(
                "2026年8月华东销售额环比如何？",
                draft(comparison="mom"),
                analysis=spec(comparison="mom"),
            )
        ],
    },
    {
        "id": "12-product-comparison",
        "name": "指定两个商品对比，后续环比继承商品",
        "turns": [
            turn(
                "比较2026年8月华东商品甲和商品乙销售额",
                draft(view="products", product_names=["商品甲", "商品乙"]),
                analysis=spec(view="products", product_ids=[1, 2]),
            ),
            turn(
                "环比呢？",
                {"follow_up": True, "comparison": "mom"},
                analysis=spec(view="products", product_ids=[1, 2], comparison="mom"),
                inherited=["metric", "months", "region", "view", "chart", "products"],
            ),
        ],
    },
    {
        "id": "13-unknown-product",
        "name": "未知商品澄清，不能扩大成全部商品",
        "turns": [
            turn(
                "比较2026年8月华东火星商品销售额",
                draft(view="products", product_unknown=True),
                fields=["products"],
            ),
            turn(
                "商品改成商品甲",
                draft(view="products", product_names=["商品甲"]),
                reply=True,
                analysis=spec(view="products", product_ids=[1]),
            ),
        ],
    },
    {
        "id": "14-anomaly-detection",
        "name": "异常月趋势：指定历史窗口和阈值",
        "turns": [
            turn(
                "检测2026年7月至8月华东销售额异常，比较前6个月均值，偏差阈值40%",
                draft(
                    period=period("2026-07", "2026-08"),
                    view="trend",
                    goal="anomalies",
                    comparison="none",
                    sort_by="value",
                    anomaly_window=6,
                    anomaly_threshold_percent=40,
                ),
                analysis=spec(
                    months=["2026-07", "2026-08"],
                    view="trend",
                    goal="anomalies",
                    anomaly_window=6,
                    anomaly_threshold_percent=40,
                ),
            )
        ],
    },
    {
        "id": "15-mom-decline",
        "name": "区域环比下降最多模板",
        "turns": [
            turn(
                "比较2026年8月各区域销售额环比，找出下降最多的区域",
                draft(
                    region_name=None,
                    region_all=True,
                    view="regions",
                    comparison="mom",
                    sort_by="mom_percent",
                    sort_order="asc",
                    top_n=1,
                    goal="largest_decline",
                ),
                analysis=spec(
                    region=0,
                    view="regions",
                    comparison="mom",
                    sort_by="mom_percent",
                    sort_order="asc",
                    top_n=1,
                    goal="largest_decline",
                ),
            )
        ],
    },
]
