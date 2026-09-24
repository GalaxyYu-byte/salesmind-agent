"""Application users and region authorization."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import AgentAppBase
from salesmind.db.entities.agent_app_common import (
    _ascii_char,
    _ascii_varchar,
    _created_at,
    _updated_at,
)

if TYPE_CHECKING:
    from salesmind.db.entities.agent_app_sessions import ChatSession


class AppUser(AgentAppBase):
    """应用登录账号及当前权限；销售员身份通过 rep_id 与业务库逻辑关联。"""

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("username", name="uk_user_username"),
        UniqueConstraint("rep_id", name="uk_user_rep"),
        CheckConstraint(
            "role IN ('SALES_REP', 'SALES_MANAGER', 'SALES_DIRECTOR')",
            name="ck_user_role",
        ),
        CheckConstraint(
            "role <> 'SALES_REP' OR rep_id IS NOT NULL", name="ck_user_rep_required"
        ),
        CheckConstraint(
            "JSON_TYPE(allowed_metrics_json) = 'ARRAY'", name="ck_user_metrics"
        ),
        CheckConstraint("permission_version > 0", name="ck_user_permission_version"),
        CheckConstraint("active IN (0, 1)", name="ck_user_active"),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "登录账号；一个销售员最多绑定一个账号，账号可不绑定销售员",
        },
    )

    id: Mapped[str] = mapped_column(_ascii_char(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        VARCHAR(64, charset="ascii", collation="ascii_general_ci"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(_ascii_varchar(255), nullable=False)
    role: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    # 跨库不建立物理外键；写入服务校验销售员存在，唯一约束防止重复绑定。
    rep_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    allowed_metrics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # 授权变更时递增，旧任务和结果凭此拒绝复用。
    permission_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("1")
    )
    active: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    region_scopes: Mapped[list[UserRegionScope]] = relationship(
        back_populates="user", lazy="raise"
    )
    sessions: Mapped[list[ChatSession]] = relationship(
        back_populates="owner", lazy="raise"
    )


class UserRegionScope(AgentAppBase):
    """用户到授权区域的关联，联合主键防止重复授权。"""

    __tablename__ = "user_region_scope"
    __table_args__ = (
        Index("idx_scope_region", "region_id"),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "经理与授权区域的多对多关联；变更时同步递增用户权限版本",
        },
    )

    user_id: Mapped[str] = mapped_column(
        _ascii_char(36),
        ForeignKey("agent_app.app_user.id", name="fk_scope_user"),
        primary_key=True,
    )
    # 区域位于 sales_data；修改授权时还需递增 app_user.permission_version。
    region_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[AppUser] = relationship(back_populates="region_scopes", lazy="raise")
