"""业务数据版本和完整性水位实体。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from salesmind.db.base import SalesDataBase


class DatasetVersion(SalesDataBase):
    """当前发布数据版本及日期水位；只保留 id=1 的单行。"""

    __tablename__ = "dataset_version"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_dataset_singleton"),
        CheckConstraint("version > 0", name="ck_dataset_version"),
        CheckConstraint(
            "complete_through IS NULL OR complete_through >= available_from",
            name="ck_dataset_dates",
        ),
        {
            "schema": "sales_data",
            "mysql_engine": "InnoDB",
            "comment": "当前业务数据版本和完整性水位，不保存历史数据快照",
        },
    )

    id: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), primary_key=True, autoincrement=False
    )
    # 导入端在更新业务数据的同一事务内递增版本。
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    available_from: Mapped[date] = mapped_column(Date, nullable=False)
    # 到该日期为止的数据已完整；空值表示尚无完整日期水位。
    complete_through: Mapped[date | None] = mapped_column(Date)
