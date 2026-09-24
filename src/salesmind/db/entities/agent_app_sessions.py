"""Chat sessions and visible messages."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from salesmind.db.base import AgentAppBase
from salesmind.db.entities.agent_app_common import (
    _ascii_char,
    _ascii_varchar,
    _created_at,
    _updated_at,
)

if TYPE_CHECKING:
    from salesmind.db.entities.agent_app_runs import AnalysisRun
    from salesmind.db.entities.agent_app_users import AppUser


class ChatSession(AgentAppBase):
    """用户会话；记录当前任务，服务通过会话行锁串行执行分析。"""

    __tablename__ = "chat_session"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uk_session_owner"),
        Index("idx_session_owner_updated", "owner_id", "updated_at", "id"),
        Index("idx_session_active", "active_run_id", "id", "owner_id"),
        ForeignKeyConstraint(
            ["owner_id"], ["agent_app.app_user.id"], name="fk_session_owner"
        ),
        # 会话与任务互相引用，建表时需后补 active_run 组合外键。
        ForeignKeyConstraint(
            ["active_run_id", "id", "owner_id"],
            [
                "agent_app.analysis_run.id",
                "agent_app.analysis_run.session_id",
                "agent_app.analysis_run.owner_id",
            ],
            name="fk_session_active_run",
            use_alter=True,
        ),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "一个用户拥有多个会话；同会话串行执行任务",
        },
    )

    id: Mapped[str] = mapped_column(_ascii_char(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("'新会话'")
    )
    active_run_id: Mapped[str | None] = mapped_column(_ascii_char(36))
    graph_version: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    # 摘要只是上下文缓存，使用前要核对权限范围和权限版本。
    context_summary: Mapped[str | None] = mapped_column(Text)
    context_scope_hash: Mapped[str | None] = mapped_column(_ascii_char(64))
    context_permission_version: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True)
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    owner: Mapped[AppUser] = relationship(back_populates="sessions", lazy="raise")
    runs: Mapped[list[AnalysisRun]] = relationship(
        back_populates="session",
        foreign_keys="AnalysisRun.session_id,AnalysisRun.owner_id",
        lazy="raise",
    )
    # 循环外键只读映射；持有会话行锁时显式更新 active_run_id。
    active_run: Mapped[AnalysisRun | None] = relationship(
        primaryjoin=(
            "and_(ChatSession.active_run_id == AnalysisRun.id, "
            "ChatSession.id == AnalysisRun.session_id, "
            "ChatSession.owner_id == AnalysisRun.owner_id)"
        ),
        foreign_keys="ChatSession.active_run_id,ChatSession.id,ChatSession.owner_id",
        viewonly=True,
        uselist=False,
        lazy="raise",
    )


class ChatMessage(AgentAppBase):
    """用户可见的对话消息；一次任务可产生多轮澄清消息。"""

    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uk_message_run_seq"),
        UniqueConstraint("run_id", "message_key", name="uk_message_run_key"),
        Index("idx_message_session_created", "session_id", "created_at", "id"),
        Index("idx_message_run_owner_fk", "run_id", "session_id", "owner_id"),
        ForeignKeyConstraint(
            ["run_id", "session_id", "owner_id"],
            [
                "agent_app.analysis_run.id",
                "agent_app.analysis_run.session_id",
                "agent_app.analysis_run.owner_id",
            ],
            name="fk_message_run_owner",
        ),
        CheckConstraint("seq > 0", name="ck_message_seq"),
        CheckConstraint(
            "(role = 'user' AND kind IN ('question', 'clarification_reply')) "
            "OR (role = 'assistant' AND kind IN ('clarification', 'answer', 'error'))",
            name="ck_message_role_kind",
        ),
        {
            "schema": "agent_app",
            "mysql_engine": "InnoDB",
            "comment": "按条保存可见消息，允许同任务中多次澄清，不保存内部推理",
        },
    )

    id: Mapped[str] = mapped_column(_ascii_char(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    session_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(_ascii_char(36), nullable=False)
    # seq 负责任务内排序，message_key 负责消息写入幂等。
    seq: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False)
    message_key: Mapped[str] = mapped_column(_ascii_varchar(128), nullable=False)
    role: Mapped[str] = mapped_column(_ascii_varchar(16), nullable=False)
    kind: Mapped[str] = mapped_column(_ascii_varchar(32), nullable=False)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    run: Mapped[AnalysisRun] = relationship(
        back_populates="messages",
        foreign_keys=[run_id, session_id, owner_id],
        lazy="raise",
    )
