"""指标原文证据与口径校验；不依赖模型声明来扩展可查询指标。"""

import re
from dataclasses import dataclass

METRIC_ALIASES = {
    "销售额": "amount",
    "销售金额": "amount",
    "销售收入": "amount",
    "卖了多少钱": "amount",
    "订单数": "count",
    "订单数量": "count",
    "订单总数": "count",
    "多少笔订单": "count",
    "毛利": "profit",
    "毛利润": "profit",
    "平均客单价": "average",
    "客单价": "average",
}
AMBIGUOUS_METRICS = {"利润", "利润额", "盈利", "收益"}
# 易被误映射到已有口径的词，作为原文侧的防错检查；其他未知词仍由原文映射拒绝。
UNSUPPORTED_METRICS = {
    "净利润",
    "净利",
    "营业利润",
    "利润总额",
    "毛利率",
    "利润率",
    "净利率",
    "转化率",
    "留存率",
    "销售量",
    "销量",
    "回款额",
    "销售目标",
}
METRIC_NAMES = "销售额、订单数、毛利、平均客单价"
_MENTIONS = re.compile(
    "(?:"
    + "|".join(
        re.escape(term)
        for term in sorted(
            METRIC_ALIASES.keys() | AMBIGUOUS_METRICS | UNSUPPORTED_METRICS,
            key=lambda term: (-len(term), term),
        )
    )
    + r")(?:率|占比)?"
)
_NEGATED = re.compile(
    r"(?:不是|(?:不再|不想|不要|不需要|不用|别)(?:看|查|统计|按)?|不(?:看|查|统计|按))\s*$"
)
_CORRECTION = re.compile(
    r"(?:改成|改为|改看|改查|改按|换成|换为|换看|而是|只看|只查|只统计|只要)\s*$"
)


@dataclass(frozen=True)
class MetricEvidence:
    text: str | None
    status: str | None
    metric: str | None


def verify_metric(text, status, metric, inputs=None) -> MetricEvidence:
    """原文决定可接受的别名；最后一次明确指标更正优先于旧问题。"""
    if inputs is not None:
        # 同时检查原文，防止模型遗漏净利润或把原文伪写成毛利后继续查询。
        source = max(
            (i for i, value in enumerate(inputs) if text and text in value), default=-1
        )
        if (
            source >= 0
            and text is not None
            and not any(
                not _NEGATED.search(inputs[source][: match.start()])
                for match in re.finditer(re.escape(text), inputs[source])
            )
        ):
            return MetricEvidence(text, "ambiguous", None)
        for index in range(len(inputs) - 1, -1, -1):
            value = inputs[index]
            all_matches = list(_MENTIONS.finditer(value))
            matches = [
                m for m in all_matches if not _NEGATED.search(value[: m.start()])
            ]
            if index < source:
                continue
            if all_matches and not matches:
                return MetricEvidence(all_matches[-1].group(), "ambiguous", None)
            if not matches:
                continue
            # 只替换明确更正的前一个指标；同句中其他仍有效的指标不能被抹掉。
            mentions = []
            previous = None
            for match in matches:
                if previous and _CORRECTION.search(
                    value[previous.end() : match.start()]
                ):
                    mentions = [term for term in mentions if term != previous.group()]
                mentions.append(match.group())
                previous = match
            if len(set(mentions)) > 1:
                return MetricEvidence(
                    "、".join(dict.fromkeys(mentions)), "unknown", None
                )
            active = mentions[-1]
            if active not in METRIC_ALIASES and active not in AMBIGUOUS_METRICS:
                if (
                    text
                    and active in text
                    and text not in METRIC_ALIASES
                    and source == index
                ):
                    return MetricEvidence(text, "unknown", None)
                return MetricEvidence(active, "unknown", None)
            if active in AMBIGUOUS_METRICS:
                return MetricEvidence(active, "ambiguous", None)
            if text is None:
                return MetricEvidence(active, "ambiguous", None)
            if text is not None and active != text:
                return MetricEvidence(active, "ambiguous", None)
            break
        if text is not None and not any(text in value for value in inputs):
            return MetricEvidence(text, "ambiguous", None)
    if text is None:
        if metric is not None or status in {"supported", "unknown", "ambiguous"}:
            return MetricEvidence(None, "ambiguous", None)
        return MetricEvidence(None, None, None)
    if text in AMBIGUOUS_METRICS:
        return MetricEvidence(text, "ambiguous", None)
    mapped = METRIC_ALIASES.get(text)
    if mapped is None:
        return MetricEvidence(text, "unknown", None)
    if status != "supported" or metric != mapped:
        return MetricEvidence(text, "ambiguous", None)
    return MetricEvidence(text, "supported", mapped)
