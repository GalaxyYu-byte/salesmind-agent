"""按已完成订单汇总销售概览，不接收原始 SQL，不写业务库。"""

import calendar
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, extract, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from salesmind.db.entities import DatasetVersion, Product, SalesOrder, SalesRegion
from salesmind.services.permissions import DataScope


class SalesQueryError(ValueError):
    """日期覆盖、范围和维度错误，可安全返回给客户端。"""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class RegionItem(BaseModel):
    id: int
    name: str


class ProductItem(RegionItem):
    category: str


class AggregateCell(BaseModel):
    month: str
    region: int
    product: int
    # 金额传十进制字符串（整数分），避免 JSON/JS 大整数损失。
    amount: str | None = None
    cost: str | None = None
    gross_profit: str | None = None
    count: int | None = None
    quantity: int | None = None


class SalesDataset(BaseModel):
    version: int
    available_from: date
    complete_through: date
    order_count: int | None = None
    regions: list[RegionItem]
    products: list[ProductItem]
    cells: list[AggregateCell]


class SalesTotals(BaseModel):
    sales_amount: str | None = None
    gross_profit: str | None = None
    order_count: int | None = None
    quantity: int | None = None


class AccessInfo(BaseModel):
    user_id: str
    permission_version: int
    scope_hash: str
    allowed_metrics: list[str]
    scope_label: str


class QueryCacheInfo(BaseModel):
    hit: bool
    ttl_seconds: int


class OverviewResponse(BaseModel):
    source: str = "mysql"
    queried_at: datetime
    months: list[str]
    region_id: int | None
    baseline_months: list[str]
    baseline_covered: bool
    comparison: Literal["none", "yoy", "mom"] = "yoy"
    history_months: list[str] = Field(default_factory=list)
    cache: QueryCacheInfo | None = None
    totals: SalesTotals
    dataset: SalesDataset
    access: AccessInfo | None = None
    metric_definition: str = "仅统计已完成订单，排除退款和取消；金额来自订单成交快照。"


class OverviewQuery(BaseModel):
    model_config = {"extra": "forbid"}
    start_month: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    end_month: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    region_id: int | None = Field(default=None, gt=0)
    comparison: Literal["none", "yoy", "mom"] = "yoy"
    baseline_mode: Literal["period", "month"] = "period"
    history_months: int = Field(default=0, ge=0, le=12)
    refresh: bool = False


def month_number(value: date) -> int:
    return value.year * 12 + value.month - 1


def month_date(number: int) -> date:
    return date(number // 12, number % 12 + 1, 1)


def comparison_months(query: OverviewQuery, months: list[str]) -> list[str]:
    """同比取去年同期；环比趋势取各月上月，汇总取等长上一期间。"""
    if query.comparison == "none":
        return []
    if query.comparison == "yoy":
        return [f"{int(m[:4]) - 1}{m[4:]}" for m in months]
    start = month_number(date.fromisoformat(months[0] + "-01"))
    offset = 1 if query.baseline_mode == "month" else len(months)
    return [
        month_date(start - offset + i).strftime("%Y-%m") for i in range(len(months))
    ]


def months_covered(months: list[str], available: date, complete: date) -> bool:
    return bool(months) and all(
        date.fromisoformat(m + "-01") >= available
        and month_date(month_number(date.fromisoformat(m + "-01")) + 1).toordinal() - 1
        <= complete.toordinal()
        for m in months
    )


def resolve_months(query: OverviewQuery, available: date, complete: date) -> list[str]:
    """默认最近六个完整月；缺失或部分月份拒绝，不补零。"""
    if bool(query.start_month) != bool(query.end_month):
        raise SalesQueryError("开始月份和结束月份需要同时提供")
    latest = month_number(complete)
    if complete.day != calendar.monthrange(complete.year, complete.month)[1]:
        latest -= 1
    if query.start_month:
        start = month_number(date.fromisoformat(query.start_month + "-01"))
        end = month_number(date.fromisoformat(query.end_month + "-01"))
    else:
        end = latest
        first = month_number(available) + (available.day != 1)
        start = max(first, end - 5)
    if end < start or end - start >= 24:
        raise SalesQueryError("请选择连续的 1 至 24 个完整月，结束月份不能早于开始月份")
    if month_date(start) < available or end > latest:
        raise SalesQueryError("所选月份未完整覆盖，请在数据水位内选择完整月份")
    return [month_date(m).strftime("%Y-%m") for m in range(start, end + 1)]


async def query_sales_overview(
    session: AsyncSession, query: OverviewQuery, scope: DataScope
) -> OverviewResponse:
    """授权过滤同时作用于当前期与基期；受限指标不进入查询结果。"""
    scope.require_data()
    if (
        query.region_id is not None
        and scope.allowed_region_ids is not None
        and query.region_id not in scope.allowed_region_ids
    ):
        raise SalesQueryError("所请求的区域不在账号授权范围内", 403)
    version = await session.get(DatasetVersion, 1)
    if version is None or version.complete_through is None:
        raise SalesQueryError("数据库尚未发布完整数据水位", 503)
    months = resolve_months(query, version.available_from, version.complete_through)
    baseline_months = comparison_months(query, months)
    regions = list(
        (
            await session.scalars(
                select(SalesRegion)
                .where(*scope.region_conditions())
                .order_by(SalesRegion.id)
            )
        ).all()
    )
    if query.region_id is not None and not any(
        r.id == query.region_id for r in regions
    ):
        if scope.allowed_rep_ids is not None or scope.allowed_region_ids is not None:
            raise SalesQueryError("所请求的区域不在账号可见范围内", 403)
        raise SalesQueryError("销售区域不存在", 404)
    product_query = select(Product).order_by(Product.id)
    if scope.allowed_rep_ids is not None or scope.allowed_region_ids is not None:
        product_query = product_query.where(
            Product.id.in_(
                select(SalesOrder.product_id).where(*scope.order_conditions())
            )
        )
    products = list((await session.scalars(product_query)).all())
    start = date.fromisoformat(months[0] + "-01")
    end = month_date(month_number(date.fromisoformat(months[-1] + "-01")) + 1)
    year = extract("year", SalesOrder.order_date)
    month = extract("month", SalesOrder.order_date)
    metrics = set(scope.allowed_metrics)
    history_months = (
        [
            month_date(month_number(start) - query.history_months + i).strftime("%Y-%m")
            for i in range(query.history_months)
        ]
        if "growth_rate" in metrics
        else []
    )

    def authorized(metric, expression, label):
        return (expression if metric in metrics else literal(None)).label(label)

    periods = [and_(SalesOrder.order_date >= start, SalesOrder.order_date < end)]
    if "growth_rate" in metrics and baseline_months:
        baseline_start = date.fromisoformat(baseline_months[0] + "-01")
        baseline_end = month_date(
            month_number(date.fromisoformat(baseline_months[-1] + "-01")) + 1
        )
        periods.append(
            and_(
                SalesOrder.order_date >= baseline_start,
                SalesOrder.order_date < baseline_end,
            )
        )
    if "growth_rate" in metrics and query.history_months:
        # 查询历史只用于滚动检测；不读取水位之前未完整覆盖的月份。
        history_start = max(
            month_date(month_number(start) - query.history_months),
            version.available_from,
        )
        periods.append(
            and_(SalesOrder.order_date >= history_start, SalesOrder.order_date < start)
        )
    statement = (
        select(
            year.label("year"),
            month.label("month"),
            SalesOrder.region_id,
            SalesOrder.product_id,
            authorized("sales_amount", func.sum(SalesOrder.amount), "amount"),
            authorized("sales_cost", func.sum(SalesOrder.cost), "cost"),
            authorized(
                "gross_profit", func.sum(SalesOrder.amount - SalesOrder.cost), "profit"
            ),
            authorized("order_count", func.count(SalesOrder.id), "count"),
            authorized("quantity", func.sum(SalesOrder.quantity), "quantity"),
        )
        .where(
            SalesOrder.status == "COMPLETED",
            *scope.order_conditions(),
            or_(*periods),
        )
        .group_by(year, month, SalesOrder.region_id, SalesOrder.product_id)
        .order_by(year, month, SalesOrder.region_id, SalesOrder.product_id)
    )
    if query.region_id is not None:
        statement = statement.where(SalesOrder.region_id == query.region_id)
    cells = [
        AggregateCell(
            month=f"{int(row.year):04}-{int(row.month):02}",
            region=row.region_id,
            product=row.product_id,
            amount=str(int(Decimal(row.amount) * 100))
            if row.amount is not None
            else None,
            cost=str(int(Decimal(row.cost) * 100)) if row.cost is not None else None,
            gross_profit=str(int(Decimal(row.profit) * 100))
            if row.profit is not None
            else None,
            count=row.count,
            quantity=row.quantity,
        )
        for row in (await session.execute(statement)).all()
    ]
    current = [c for c in cells if c.month in months]

    def total_money(field, metric):
        if metric not in metrics:
            return None
        value = sum(int(getattr(c, field)) for c in current)
        return f"{Decimal(value) / 100:.2f}"

    count = None
    if "order_count" in metrics:
        count_query = select(func.count(SalesOrder.id)).where(*scope.order_conditions())
        if query.region_id is not None:
            count_query = count_query.where(SalesOrder.region_id == query.region_id)
        count = await session.scalar(count_query)
    return OverviewResponse(
        queried_at=datetime.now(UTC),
        months=months,
        region_id=query.region_id,
        baseline_months=baseline_months,
        baseline_covered="growth_rate" in metrics
        and months_covered(
            baseline_months, version.available_from, version.complete_through
        ),
        comparison=query.comparison,
        history_months=history_months,
        access=scope.public(),
        totals=SalesTotals(
            sales_amount=total_money("amount", "sales_amount"),
            gross_profit=total_money("gross_profit", "gross_profit"),
            order_count=sum(c.count for c in current)
            if "order_count" in metrics
            else None,
            quantity=sum(c.quantity for c in current)
            if "quantity" in metrics
            else None,
        ),
        dataset=SalesDataset(
            version=version.version,
            available_from=version.available_from,
            complete_through=version.complete_through,
            order_count=count,
            regions=[RegionItem(id=r.id, name=r.name) for r in regions],
            products=[
                ProductItem(id=p.id, name=p.name, category=p.category) for p in products
            ],
            cells=cells,
        ),
    )
