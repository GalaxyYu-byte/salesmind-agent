"""会话与任务协议；自然语言在后端解析，兼容已有结构化查询。"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from salesmind.services.sales_overview import OverviewQuery, month_number


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisScope(StrictModel):
    months: list[str] = Field(min_length=1, max_length=24)
    region: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def continuous_months(self):
        # 校验每个月，禁止客户端利用首尾月份隐藏中间的不连续范围。
        numbers = []
        for month in self.months:
            OverviewQuery(start_month=month, end_month=month)
            numbers.append(month_number(date.fromisoformat(month + "-01")))
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise ValueError("请选择连续且按顺序排列的完整月份")
        return self


class AnalysisSpec(StrictModel):
    scope: AnalysisScope
    metric: Literal["amount", "count", "profit", "average"] = "amount"
    view: Literal["summary", "trend", "products", "regions"] = "trend"
    chart: Literal["line", "bar"] = "line"
    comparison: Literal["none", "yoy", "mom"] = "none"
    sort_by: Literal[
        "value", "yoy_percent", "yoy_change", "mom_percent", "mom_change"
    ] = "value"
    sort_order: Literal["asc", "desc"] = "desc"
    top_n: int | None = Field(default=None, ge=1, le=20, strict=True)
    goal: Literal["overview", "largest_decline", "anomalies"] = "overview"
    product_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(
        default_factory=list, max_length=20
    )
    anomaly_window: int = Field(default=3, ge=3, le=12, strict=True)
    anomaly_threshold_percent: float = Field(default=30, gt=0, le=1000)

    @model_validator(mode="after")
    def supported_analysis(self):
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("商品 ID 不得重复")
        if self.product_ids and not (
            self.view == "products"
            or (self.view == "trend" and self.goal == "anomalies")
        ):
            raise ValueError("指定商品仅支持商品对比或商品异常趋势")
        if self.goal == "anomalies" and (
            self.view != "trend"
            or self.comparison != "none"
            or self.sort_by != "value"
            or self.sort_order != "desc"
            or self.top_n is not None
        ):
            raise ValueError("异常检测需要完整月份趋势，不使用同比、环比或排名筛选")
        if self.goal == "largest_decline":
            if self.top_n is None:
                self.top_n = 1
            elif self.top_n != 1:
                raise ValueError("下降最多仅选择第一名；前 N 名请使用普通排序")
        if self.sort_by != "value" and (
            self.comparison != self.sort_by.split("_", 1)[0]
            or self.view not in {"products", "regions"}
        ):
            raise ValueError(
                "增长率排序需要按商品或区域分组，并开启对应的同比或环比比较"
            )
        if self.top_n is not None and self.view not in {"products", "regions"}:
            raise ValueError("前 N 名仅支持商品或区域排名")
        if self.goal == "largest_decline" and (
            self.view != "regions"
            or self.comparison not in {"yoy", "mom"}
            or self.sort_by != f"{self.comparison}_percent"
            or self.sort_order != "asc"
            or self.scope.region != 0
        ):
            raise ValueError("下降最多需要对全部区域按同比或环比下降百分比升序比较")
        return self

    def overview_query(self) -> OverviewQuery:
        return OverviewQuery(
            start_month=self.scope.months[0],
            end_month=self.scope.months[-1],
            region_id=self.scope.region or None,
            comparison=self.comparison,
            baseline_mode="month" if self.view == "trend" else "period",
            history_months=self.anomaly_window if self.goal == "anomalies" else 0,
        )


class SessionCreate(StrictModel):
    title: str = Field(default="新的分析", min_length=1, max_length=200)


class RunCreate(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["auto", "analysis", "clarify", "unsupported"] | None = None
    analysis: AnalysisSpec | None = None
    explanation: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_mode(self):
        if self.mode is None:
            self.mode = "analysis" if self.analysis is not None else "auto"
        if (self.mode == "analysis") != (self.analysis is not None):
            raise ValueError(
                "分析任务必须提供结构化参数；澄清与不支持请求不能带分析参数"
            )
        return self


class RunResume(StrictModel):
    analysis: AnalysisSpec | None = None
    reply: str | None = Field(default=None, min_length=1, max_length=2000)
