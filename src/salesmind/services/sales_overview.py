"""按已完成订单汇总销售概览，不接收原始 SQL，不写业务库。"""

import calendar
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from salesmind.db.entities import DatasetVersion, Product, SalesOrder, SalesRegion


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
    amount: str
    cost: str
    count: int
    quantity: int


class SalesDataset(BaseModel):
    version: int
    available_from: date
    complete_through: date
    order_count: int
    regions: list[RegionItem]
    products: list[ProductItem]
    cells: list[AggregateCell]


class SalesTotals(BaseModel):
    sales_amount: str
    gross_profit: str
    order_count: int
    quantity: int


class OverviewResponse(BaseModel):
    source: str = "mysql"
    queried_at: datetime
    months: list[str]
    region_id: int | None
    baseline_months: list[str]
    baseline_covered: bool
    totals: SalesTotals
    dataset: SalesDataset
    metric_definition: str = "仅统计已完成订单，排除退款和取消；金额来自订单成交快照。"


class OverviewQuery(BaseModel):
    model_config = {"extra": "forbid"}
    start_month: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    end_month: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    region_id: int | None = Field(default=None, gt=0)


def month_number(value: date) -> int:
    return value.year * 12 + value.month - 1


def month_date(number: int) -> date:
    return date(number // 12, number % 12 + 1, 1)


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
    session: AsyncSession, query: OverviewQuery
) -> OverviewResponse:
    """在调用方的只读事务中读取水位、维度及分月分区域分商品聚合。"""
    version = await session.get(DatasetVersion, 1)
    if version is None or version.complete_through is None:
        raise SalesQueryError("数据库尚未发布完整数据水位", 503)
    months = resolve_months(query, version.available_from, version.complete_through)
    baseline_months = [f"{int(m[:4]) - 1}{m[4:]}" for m in months]
    regions = list(
        (await session.scalars(select(SalesRegion).order_by(SalesRegion.id))).all()
    )
    if query.region_id is not None and not any(
        r.id == query.region_id for r in regions
    ):
        raise SalesQueryError("销售区域不存在", 404)
    products = list((await session.scalars(select(Product).order_by(Product.id))).all())
    start = date.fromisoformat(months[0] + "-01")
    end = month_date(month_number(date.fromisoformat(months[-1] + "-01")) + 1)
    baseline_start = month_date(month_number(start) - 12)
    baseline_end = month_date(month_number(end) - 12)
    year = extract("year", SalesOrder.order_date)
    month = extract("month", SalesOrder.order_date)
    statement = (
        select(
            year.label("year"),
            month.label("month"),
            SalesOrder.region_id,
            SalesOrder.product_id,
            func.sum(SalesOrder.amount).label("amount"),
            func.sum(SalesOrder.cost).label("cost"),
            func.count(SalesOrder.id).label("count"),
            func.sum(SalesOrder.quantity).label("quantity"),
        )
        .where(
            SalesOrder.status == "COMPLETED",
            or_(
                and_(SalesOrder.order_date >= start, SalesOrder.order_date < end),
                and_(
                    SalesOrder.order_date >= baseline_start,
                    SalesOrder.order_date < baseline_end,
                ),
            ),
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
            amount=str(int(Decimal(row.amount) * 100)),
            cost=str(int(Decimal(row.cost) * 100)),
            count=row.count,
            quantity=row.quantity,
        )
        for row in (await session.execute(statement)).all()
    ]
    current = [c for c in cells if c.month in months]
    amount = sum(int(c.amount) for c in current)
    cost = sum(int(c.cost) for c in current)
    count = await session.scalar(select(func.count(SalesOrder.id)))
    return OverviewResponse(
        queried_at=datetime.now(UTC),
        months=months,
        region_id=query.region_id,
        baseline_months=baseline_months,
        baseline_covered=baseline_start >= version.available_from,
        totals=SalesTotals(
            sales_amount=f"{Decimal(amount) / 100:.2f}",
            gross_profit=f"{Decimal(amount - cost) / 100:.2f}",
            order_count=sum(c.count for c in current),
            quantity=sum(c.quantity for c in current),
        ),
        dataset=SalesDataset(
            version=version.version,
            available_from=version.available_from,
            complete_through=version.complete_through,
            order_count=count or 0,
            regions=[RegionItem(id=r.id, name=r.name) for r in regions],
            products=[
                ProductItem(id=p.id, name=p.name, category=p.category) for p in products
            ],
            cells=cells,
        ),
    )
