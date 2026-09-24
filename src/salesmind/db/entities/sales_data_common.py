"""sales_data 实体共用的审计时间列。"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column


def _audit_created() -> Mapped[datetime]:
    """创建时间由 MySQL 生成，精确到毫秒。"""
    return mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )


def _audit_updated() -> Mapped[datetime]:
    """修改时间由 MySQL 在插入和更新时维护。"""
    return mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
