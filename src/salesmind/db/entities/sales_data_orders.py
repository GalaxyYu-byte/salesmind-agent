"""销售订单事实实体。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT, INTEGER, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import SalesDataBase
from salesmind.db.entities.sales_data_common import _audit_created, _audit_updated

if TYPE_CHECKING:
    from salesmind.db.entities.sales_data_dimensions import (
        Product,
        SalesRegion,
        SalesRep,
    )


class SalesOrder(SalesDataBase):
    """销售事实表；一行代表一笔单商品订单。"""

    __tablename__ = "sales_order"
    __table_args__ = (
        UniqueConstraint("order_no", name="uk_order_no"),
        Index("idx_order_region_status_date", "region_id", "status", "order_date"),
        Index("idx_order_rep_status_date", "rep_id", "status", "order_date"),
        Index("idx_order_status_date_product", "status", "order_date", "product_id"),
        Index("idx_order_product_status_date", "product_id", "status", "order_date"),
        Index("idx_order_date", "order_date"),
        CheckConstraint("quantity > 0", name="ck_order_quantity"),
        CheckConstraint(
            "unit_price >= 0 AND amount >= 0 AND cost >= 0", name="ck_order_amounts"
        ),
        CheckConstraint(
            "amount = ROUND(quantity * unit_price, 2)", name="ck_order_amount_formula"
        ),
        CheckConstraint(
            "status IN ('COMPLETED', 'REFUNDED', 'CANCELLED')", name="ck_order_status"
        ),
        {
            "schema": "sales_data",
            "mysql_engine": "InnoDB",
            "comment": "销售事实表，一行一个单商品订单，不独立存储毛利",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    order_no: Mapped[str] = mapped_column(
        VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=False
    )
    rep_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("sales_data.sales_rep.id", name="fk_order_rep"),
        nullable=False,
    )
    # 成交区域快照，不随销售员之后的区域调动而变化。
    region_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("sales_data.sales_region.id", name="fk_order_region"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("sales_data.product.id", name="fk_order_product"),
        nullable=False,
    )
    customer_name: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 金额由数量和单价按两位小数计算；毛利用 amount - cost 派生。
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 业绩统计仅计 COMPLETED；退款和取消记录仍保留在事实表中。
    status: Mapped[str] = mapped_column(
        VARCHAR(20, charset="ascii", collation="ascii_bin"), nullable=False
    )
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = _audit_created()
    updated_at: Mapped[datetime] = _audit_updated()

    rep: Mapped[SalesRep] = relationship(back_populates="orders", lazy="raise")
    region: Mapped[SalesRegion] = relationship(back_populates="orders", lazy="raise")
    product: Mapped[Product] = relationship(back_populates="orders", lazy="raise")
