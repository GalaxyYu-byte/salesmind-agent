"""销售区域、销售员和商品维度实体。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import SalesDataBase
from salesmind.db.entities.sales_data_common import _audit_created, _audit_updated

if TYPE_CHECKING:
    from salesmind.db.entities.sales_data_orders import SalesOrder


class SalesRegion(SalesDataBase):
    """销售区域层级；数据库维护父区域外键，写入服务拒绝循环。"""

    __tablename__ = "sales_region"
    __table_args__ = (
        UniqueConstraint("name", name="uk_region_name"),
        Index("idx_region_parent", "parent_id"),
        CheckConstraint("active IN (0, 1)", name="ck_region_active"),
        {
            "schema": "sales_data",
            "mysql_engine": "InnoDB",
            "comment": "销售区域；父子循环由写入服务拒绝",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    # 根区域没有父区域；自引用关系的 remote_side 指向父节点。
    parent_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("sales_data.sales_region.id", name="fk_region_parent"),
    )
    active: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = _audit_created()
    updated_at: Mapped[datetime] = _audit_updated()

    parent: Mapped[SalesRegion | None] = relationship(
        remote_side="SalesRegion.id", back_populates="children", lazy="raise"
    )
    children: Mapped[list[SalesRegion]] = relationship(
        back_populates="parent", lazy="raise"
    )
    reps: Mapped[list[SalesRep]] = relationship(back_populates="region", lazy="raise")
    orders: Mapped[list[SalesOrder]] = relationship(
        back_populates="region", lazy="raise"
    )


class SalesRep(SalesDataBase):
    """销售员业务维度；登录认证由 agent_app.app_user 负责。"""

    __tablename__ = "sales_rep"
    __table_args__ = (
        Index("idx_rep_region", "region_id"),
        CheckConstraint("active IN (0, 1)", name="ck_rep_active"),
        {
            "schema": "sales_data",
            "mysql_engine": "InnoDB",
            "comment": "销售员维度，不承担登录认证",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    region_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("sales_data.sales_region.id", name="fk_rep_region"),
        nullable=False,
    )
    active: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = _audit_created()
    updated_at: Mapped[datetime] = _audit_updated()

    region: Mapped[SalesRegion] = relationship(back_populates="reps", lazy="raise")
    orders: Mapped[list[SalesOrder]] = relationship(back_populates="rep", lazy="raise")


class Product(SalesDataBase):
    """商品维度；成交金额和成本以订单快照为准。"""

    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("sku", name="uk_product_sku"),
        Index("idx_product_category", "category"),
        CheckConstraint("active IN (0, 1)", name="ck_product_active"),
        {
            "schema": "sales_data",
            "mysql_engine": "InnoDB",
            "comment": "商品维度；统计使用订单中的实际成交金额与成本",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), primary_key=True, autoincrement=True
    )
    # SKU 按字节区分大小写，避免不同编码被折叠为同一商品。
    sku: Mapped[str] = mapped_column(
        VARCHAR(50, charset="ascii", collation="ascii_bin"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    active: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = _audit_created()
    updated_at: Mapped[datetime] = _audit_updated()

    orders: Mapped[list[SalesOrder]] = relationship(
        back_populates="product", lazy="raise"
    )
