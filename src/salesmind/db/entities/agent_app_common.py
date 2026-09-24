"""agent_app 实体共用的字符类型和审计时间列。"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.mysql import CHAR, DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column


def _ascii_char(length: int) -> CHAR:
    """UUID 和哈希使用区分大小写的定长 ASCII 字符串。"""
    return CHAR(length, charset="ascii", collation="ascii_bin")


def _ascii_varchar(length: int) -> VARCHAR:
    """业务编码使用区分大小写的变长 ASCII 字符串。"""
    return VARCHAR(length, charset="ascii", collation="ascii_bin")


def _created_at() -> Mapped[datetime]:
    """创建时间由 MySQL 生成，精确到毫秒。"""
    return mapped_column(
        DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")
    )


def _updated_at() -> Mapped[datetime]:
    """修改时间由 MySQL 在插入和更新时维护。"""
    return mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
